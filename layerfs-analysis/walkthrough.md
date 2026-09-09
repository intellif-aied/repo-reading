# LayerFS: a linear source walkthrough

*2026-09-09T04:31:42Z by Showboat 0.6.1*
<!-- showboat-id: 422ee149-ef76-416b-9aaf-616b1878d6d9 -->

This walkthrough follows the implementation in this checkout. Read it in order: each stop supplies the concepts needed by the next. The excerpts are actual command output captured by Showboat; the commands run from the repository root and only read source files. They can be replayed with `uvx showboat --workdir ../layerfs verify walkthrough.md`.

The route is: public lifecycle → CLI ownership → SDK composition → immutable object identity → file and namespace trees → SQLite storage → initialization and branching → mutable workspace state → projection and execution → candidate construction → publication and recovery → reconciliation and promotion → queries, monitoring, and tests.

Our running example starts an empty LayerStack, forks a Branch, creates a Workspace, writes `hello`, commits it, promotes the Branch with Add, and ends the Workspace. A Layer or Commit refers to a complete filesystem root. The root shares unchanged objects with previous roots; reading a snapshot does not require replaying its entire history.

This is a source explanation, not a runtime qualification report. The captured commands prove which code was quoted; they do not mount FUSE, run Docker, execute the Rust tests, or establish crash durability.

```bash
git rev-parse HEAD
```

```output
46308986aec091337573227ede6c35aff7db11f2
```

## 1. Start with the workspace and the public lifecycle

The Cargo workspace contains ten implementation crates, a small evaluator, and the benchmark package. `layerfs-content` defines immutable data and algorithms; `layerfs-layerstack-store` persists and publishes it; `layerfs-workspace-core` models mutable state; `layerfs-workspace` connects that state to storage and operating-system resources. FUSE, materialization, and the daemon provide the projection and execution mechanisms. The SDK composes these services and the CLI exposes them.

The existing SDK lifecycle test is a compact map of the whole system. It creates a Store and Client, initializes an empty LayerStack, forks from its genesis Layer, and explicitly selects a host materialization. An ordinary filesystem write changes the projection. Commit creates a Commit and advances the Branch; Add creates a Layer; clean end removes the ephemeral workspace. Keep these three final operations separate as we descend into their implementation.

```bash
sed -n '1,31p' Cargo.toml
```

```output
[workspace]
resolver = "2"
members = [
    "crates/layerfs-content",
    "crates/layerfs-daemon",
    "crates/layerfs-layerstack-store",
    "crates/layerfs-sdk",
    "crates/layerfs-workspace",
    "crates/layerfs-workspace-core",
    "crates/layerfs-fuse",
    "crates/layerfs-materialization",
    "crates/layerfs-monitor",
    "crates/layerfs-cli",
    "tools/layerfs-eval",
    "benchmark/fs-bench-pro",
]

[workspace.package]
edition = "2021"
version = "0.1.3"
rust-version = "1.85"
license = "MIT"

[workspace.dependencies]
blake3 = { version = "=1.8.5", default-features = false }
rusqlite = { version = "=0.40.2", features = ["cache", "hooks", "trace", "limits", "blob"] }
clap = { version = "=4.5.48", features = ["derive"] }
```

```bash
sed -n '8,54p' crates/layerfs-sdk/tests/v4.rs
```

```output
#[test]
fn public_sdk_runs_one_store_workspace_commit_and_add_lifecycle() {
    let root = temp("lifecycle");
    let store = Arc::new(LayerStackStore::create(root.join("store.sqlite")).unwrap());
    let client = Client::connect(store.clone()).unwrap();
    let initialized = client
        .initialize_layerstack(
            EntityName::new("project").unwrap(),
            LayerStackInitialization::Empty,
        )
        .unwrap();
    let branch_id = client
        .fork_branch(
            EntityName::new("main").unwrap(),
            LocalForkSource::Layer {
                layer_id: initialized.genesis_layer_id,
            },
        )
        .unwrap();
    let mount = root.join("view");
    let session = client
        .create_workspace_session(CreateWorkspaceSession {
            branch_id,
            placement: WorkspacePlacement::Host {
                root: mount.clone(),
            },
            projection: Some(WorkspaceProjection::Materialize),
        })
        .unwrap();
    std::fs::write(mount.join("hello"), b"world").unwrap();
    let commit_id = match client.commit_workspace_session(session.id).unwrap() {
        WorkspaceCommitResult::Created { commit_id, .. } => commit_id,
        result => panic!("unexpected Commit: {result:?}"),
    };
    let branches = client.query(Query::new(QueryKind::Branches)).unwrap().items;
    assert!(branches.iter().any(|item| {
        matches!(item, QueryItem::Branch(branch) if branch.id == branch_id && branch.head_commit_id == Some(commit_id))
    }));
    assert!(matches!(
        client.add_layer(branch_id).unwrap(),
        AddLayerResult::Added { .. }
    ));
    client
        .end_workspace_session(session.id, EndWorkspaceMode::Clean)
        .unwrap();

    drop(client);
```

## 2. The CLI entry point and the process that retains workspace state

`layerfs` reads OS arguments, handles help/version, strips an optional leading `--json`, and dispatches through `invoke_managed`. Parsing and command routing live in `layerfs-cli/src/lib.rs`: `Command` is the typed request, `CliSession` retains the selected Store and lazily created Client, and `execute_sdk` maps commands to SDK calls. The JSON mode wraps the textual result; it is not a typed JSON serialization of every domain record.

A workspace must outlive a single command invocation. On Unix, `runtime::dispatch` therefore routes workspace commands to a context-owner process over a Unix socket. Commands that start a workspace/container can start this owner; commands requiring an existing owner fail if none is active. Local database/context operations can execute directly. `serve` holds an `Arc<CliSession>` and handles socket requests, which preserves the Client, sessions, mounts, and execution handles between CLI invocations. This context owner is distinct from the container daemon discussed later.

```bash
sed -n '1,32p' crates/layerfs-cli/src/bin/layerfs.rs
```

```output
fn main() {
    let mut arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    if arguments.len() == 1 && arguments[0] == "--help" {
        print!("{}", layerfs_cli::HELP);
        return;
    }
    if arguments.len() == 1 && arguments[0] == "--version" {
        println!("layerfs {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    let json = arguments.first().is_some_and(|value| value == "--json");
    if json {
        arguments.remove(0);
    }
    #[cfg(unix)]
    let result = if arguments.first().and_then(|value| value.to_str())
        == Some("__layerfs_context_owner")
        && arguments.len() == 2
    {
        layerfs_cli::serve_context_owner(arguments.remove(1).into()).map(|()| 0)
    } else {
        layerfs_cli::invoke_managed(
            layerfs_cli::default_context_location(),
            arguments,
            json,
            &mut std::io::stdout(),
        )
    };
    #[cfg(not(unix))]
    let result = layerfs_cli::invoke_managed(
        layerfs_cli::default_context_location(),
        arguments,
```

```bash
sed -n '23,54p' crates/layerfs-cli/src/runtime.rs
```

```output
pub(crate) fn dispatch(
    context: &Path,
    arguments: Vec<OsString>,
    json: bool,
    output: &mut dyn Write,
) -> CliResult<i32> {
    let family = command_family(&arguments);
    if matches!(family, Family::Local) {
        if owner_active(context) && is_context_change(&arguments) {
            writeln!(
                output,
                "FAILED stop the active container or Workspace before changing context"
            )?;
            return Ok(1);
        }
        return crate::invoke(context, arguments, json, output);
    }
    if owner_active(context) {
        return remote(context, &arguments, json, output);
    }
    if matches!(family, Family::StartOwner) {
        start_owner(context)?;
        return remote(context, &arguments, json, output);
    }
    if matches!(family, Family::RequiresOwner) {
        writeln!(
            output,
            "FAILED no active Workspace context; create a Workspace or start its container first"
        )?;
        return Ok(1);
    }
    crate::invoke(context, arguments, json, output)
```

## 3. The SDK binds one Store, workspace manager, and Monitor

`Client` is a cloneable `Arc<ClientInner>`. Cloning it shares one Store, one `Workspaces` manager, and one Monitor. Connection creates a unique temporary runtime directory using the process ID and an atomic sequence, then chooses a workspace manager with or without a container binding. The SQLite Store remains a separate, caller-supplied object.

Methods such as `initialize_layerstack`, `fork_branch`, and `create_workspace_session` delegate to their owning component through observation wrappers. The wrappers associate results with semantic operation families and gather receipts. Thus the SDK is the public orchestration boundary, while object encoding, branch updates, and mount state remain owned by lower layers. Errors retain Store, Workspace, and Monitor categories through `SdkError`.

Next we follow what those services share: the identity of an immutable filesystem state.

```bash
sed -n '24,32p' crates/layerfs-sdk/src/client.rs
```

```output
#[derive(Clone)]
pub struct Client(Arc<ClientInner>);

struct ClientInner {
    store: Arc<LayerStackStore>,
    workspaces: Arc<Workspaces>,
    monitor: Arc<Monitor>,
}

```

```bash
sed -n '53,102p' crates/layerfs-sdk/src/client.rs
```

```output
    pub fn connect(store: Arc<LayerStackStore>) -> Result<Self> {
        Self::connect_inner(store, None)
    }

    pub fn connect_with_container(
        store: Arc<LayerStackStore>,
        binding: ContainerBinding,
    ) -> Result<Self> {
        Self::connect_inner(store, Some(binding))
    }

    fn connect_inner(
        store: Arc<LayerStackStore>,
        binding: Option<ContainerBinding>,
    ) -> Result<Self> {
        static NEXT_RUNTIME: AtomicU64 = AtomicU64::new(0);
        let runtime_root = std::env::temp_dir().join("layerfs-runtime").join(format!(
            "{}-{}",
            std::process::id(),
            NEXT_RUNTIME.fetch_add(1, Ordering::Relaxed)
        ));
        let workspaces = Arc::new(match binding {
            Some(binding) => {
                Workspaces::new_with_container(runtime_root, store.as_ref().clone(), binding)?
            }
            None => Workspaces::new(runtime_root, store.as_ref().clone())?,
        });
        let monitor = Arc::new(Monitor::new(store.clone(), workspaces.clone()));
        Ok(Self(Arc::new(ClientInner {
            store,
            workspaces,
            monitor,
        })))
    }

    pub fn initialize_layerstack(
        &self,
        name: EntityName,
        source: LayerStackInitialization,
    ) -> Result<InitializeLayerStackResult> {
        let operation = SemanticOperation {
            name: Some(name.clone()),
            ..SemanticOperation::new(OperationFamily::LayerStackInitialize)
        };
        self.observe(
            operation,
            || self.0.store.initialize_layerstack(name, source),
            |_| OperationOutcome::Success,
        )
    }
```

## 4. Content identity is a hash of canonical bytes

`ObjectId` is a 32-byte digest. `ObjectId::for_bytes` hashes an object's canonical representation, and the hashing code prefixes an object-specific domain before the bytes. A separate domain is used for logical content-byte digests. A raw file digest and a structural object ID therefore serve different purposes.

The outer object codec writes `LFSO`, a one-byte kind, and a big-endian payload length. Byte objects also encode the byte-string length. Decoding rejects malformed lengths, wrong roles, and trailing data. Higher-level file, inode, directory, and metadata codecs put their own structured records into this representation. Equal canonical bytes imply equal object IDs regardless of the later physical compression choice.

`ObjectRead` and `ObjectStore` let these algorithms run without knowing SQLite. The authenticated read path recomputes identity before handing bytes to a decoder. Store implementations can optimize batching and buffer ownership while preserving that contract.

```bash
sed -n '5,7p' crates/layerfs-content/src/object/digest.rs
```

```output
pub const DIGEST_BYTES: usize = 32;
const OBJECT_DOMAIN: &[u8] = b"layerfs/object/v2\0";
const CONTENT_DOMAIN: &[u8] = b"layerfs/content-bytes/v1\0";
```

```bash
sed -n '42,56p' crates/layerfs-content/src/object/digest.rs
```

```output
pub(crate) struct ObjectHashWriter {
    hasher: blake3::Hasher,
}

impl ObjectHashWriter {
    pub(crate) fn new() -> Self {
        let mut hasher = blake3::Hasher::new();
        hasher.update(OBJECT_DOMAIN);
        Self { hasher }
    }

    pub(crate) fn finish(self) -> [u8; DIGEST_BYTES] {
        *self.hasher.finalize().as_bytes()
    }
}
```

```bash
sed -n '11,36p' crates/layerfs-content/src/object/codec.rs
```

```output
pub const MAGIC: [u8; 4] = *b"LFSO";
pub const HEADER_LEN: usize = 9;

pub fn encode_object(object: &Object) -> CoreResult<Vec<u8>> {
    let payload_len = payload_len(object)?;
    let total_len = checked_total_len(payload_len)?;
    let mut output = Vec::with_capacity(total_len);
    encode_object_to(object, &mut output)?;
    Ok(output)
}

pub fn encode_bytes_object(value: &[u8]) -> CoreResult<Vec<u8>> {
    let payload_len = bytes_payload_len(value)?;
    let total_len = checked_total_len(payload_len)?;
    let mut output = Vec::with_capacity(total_len);
    encode_bytes_object_to(value, &mut output)?;
    Ok(output)
}

pub fn encode_bytes_object_to<W: Write>(value: &[u8], writer: &mut W) -> CoreResult<()> {
    let payload_len = bytes_payload_len(value)?;
    checked_total_len(payload_len)?;
    encode_header_to(ObjectKind::Bytes, payload_len, writer)?;
    let length = u32::try_from(value.len()).map_err(|_| CoreError::LengthOverflow)?;
    write(writer, &length.to_be_bytes())?;
    write(writer, value)
```

```bash
sed -n '1,25p' crates/layerfs-content/src/object/access.rs
```

```output
use crate::{decode_bytes_object, CoreError, CoreResult, ObjectId};

pub trait ObjectRead {
    fn get(&self, id: ObjectId) -> CoreResult<Vec<u8>>;

    fn with_authenticated_canonical<T, F>(&self, id: ObjectId, callback: F) -> CoreResult<T>
    where
        F: FnOnce(&[u8]) -> CoreResult<T>,
    {
        let bytes = self.get(id)?;
        if ObjectId::for_bytes(&bytes) != id {
            return Err(CoreError::IdentityMismatch);
        }
        callback(&bytes)
    }

    fn get_authenticated_batch<F>(&self, ids: &[ObjectId], mut callback: F) -> CoreResult<()>
    where
        F: FnMut(ObjectId, &[u8]) -> CoreResult<()>,
    {
        for id in ids {
            self.with_authenticated_canonical(*id, |canonical| {
                callback(*id, decode_bytes_object(canonical)?)
            })?;
        }
```

## 5. Turn a byte stream into reusable chunks and an extent tree

The frozen FastCDC profile uses an 8 KiB minimum, 16 KiB target, and 32 KiB maximum chunk size. Its rolling GEAR hash chooses boundaries from the bytes, subject to those bounds. Because the scanner retains state across reads, the caller's input-buffer fragmentation does not determine chunk boundaries. The profile ID hashes the parameters and GEAR table, making the chosen chunking rules part of the format.

`rope::build_complete` streams input through this scanner. Each chunk becomes a payload object. Extent slices reference a payload ID plus an offset and length; leaf and internal mapping nodes organize those slices into a bounded tree. A `FileStateV3` then records logical length, extent count, tree level, profile, and mapping root. Even an empty file gets a valid empty mapping leaf.

This separates file position from payload identity. The same payload can be reused in multiple files or snapshots, and a file region can refer to a slice of an existing payload. `rope/read.rs` uses subtree byte counts to navigate ranges, authenticating the objects it demands.

```bash
sed -n '7,16p' crates/layerfs-content/src/file/cdc/gear.rs
```

```output
pub const MINIMUM_CHUNK_BYTES: usize = 8_192;
pub const TARGET_CHUNK_BYTES: usize = 16_384;
pub const MAXIMUM_CHUNK_BYTES: usize = 32_768;
pub const NORMALIZATION_SHIFT: u32 = 2;
pub const PROFILE_SEED: u64 = 0;

const SMALL_MASK: u64 = 0x0000_d903_0353_7000;
const LARGE_MASK: u64 = 0x0000_d901_0353_0000;
const SHIFTED_SMALL_MASK: u64 = 0x0001_b206_06a6_e000;
const SHIFTED_LARGE_MASK: u64 = 0x0001_b202_06a6_0000;
```

```bash
sed -n '31,54p' crates/layerfs-content/src/file/rope/build.rs
```

```output
pub fn build_complete<S: ObjectStore, R: Read>(
    store: &mut S,
    source: R,
) -> CoreResult<CompletedFile> {
    let (root, mut counters) = build_mapping(store, source)?;
    let root = match root {
        Some(root) => root,
        None => emit_leaf(store, Vec::new(), &mut counters)?,
    };
    let state = FileStateV3 {
        logical_len: root.bytes,
        extent_count: root.extents,
        tree_level: root.level,
        profile_id: profile_id(),
        mapping_root: root.id,
    };
    let canonical = encode_file_state(state)?;
    let id = store.put_owned(canonical)?;
    Ok(CompletedFile {
        root: FileStateRoot(id),
        logical_len: state.logical_len,
        counters,
    })
}
```

```bash
sed -n '117,143p' crates/layerfs-content/src/file/rope/build.rs
```

```output
            canonical,
            counters.payload_bytes_written,
            chunk.len() as u32,
        )?;
        counters.payload_bytes_written = add(counters.payload_bytes_written, chunk.len() as u64)?;
        counters.chunks_created = add(counters.chunks_created, 1)?;
        match &mut levels[0] {
            Pending::Extents(extents) => {
                extents.push(ExtentSliceV3::new(payload, 0, chunk.len() as u32)?)
            }
            Pending::Children(_) => unreachable!(),
        }
        flush_streaming(store, &mut levels, 0, &mut counters)
    })?;
    counters.cdc_bytes_scanned = cdc.bytes_scanned;
    Ok((levels, counters, cdc.bytes_scanned))
}

pub(super) fn scan_replacement_mapping_with<S, R, FP, FN>(
    store: &mut S,
    source: R,
    origin: u64,
    mut put_payload: FP,
    mut put_sealed_node: FN,
) -> CoreResult<ReplacementScan>
where
    S: ObjectStore,
```

## 6. Replace a file range by splitting and joining the rope

The central edit operation takes an old file root, a start, a deletion length, and a replacement reader. It checks arithmetic overflow and verifies that the deletion stays inside the old file. Only the replacement stream is scanned into new chunks here; the existing file is split structurally around the changed range.

Read the sequence literally: construct the middle, split the old mapping at `start`, split the tail at `delete_len`, concatenate left + middle + right, and publish a new FileState. Deferred nodes let the algorithm discard temporary structural objects and retain the final reachable mapping. Untouched payloads and tree regions keep their IDs.

This is more precise than saying that every edit rechunks the complete file. Full-file construction and incremental splicing are separate paths. `FileMutationBatch`, immediately below this function, combines ordered edits in a private overlay so only the final reachable extent objects are selected. Later, the workspace converts its mutable pieces into these edits.

```bash
sed -n '49,103p' crates/layerfs-content/src/file/rope/edit.rs
```

```output
    let mut counters = RopeCounters::default();
    let old = state(store, root, &mut counters)?;
    counters.tree_level_before = Some(old.tree_level);
    counters.logical_len_before = Some(old.logical_len);
    let end = start
        .checked_add(delete_len)
        .ok_or(CoreError::LengthOverflow)?;
    if end > old.logical_len {
        return Err(CoreError::InvalidRange {
            start,
            end,
            length: old.logical_len,
        });
    }
    let old_summary = Summary {
        id: old.mapping_root,
        bytes: old.logical_len,
        extents: old.extent_count,
        level: old.tree_level,
    };
    let scan =
        scan_replacement_mapping_with(store, replacement, start, put_payload, put_sealed_node)?;
    merge_counters(&mut counters, scan.counters)?;
    let persisted_nodes = scan.persisted_nodes;
    let mut levels = scan.levels;
    let mut deferred = DeferredNodes::with_nodes(store, scan.pending);
    let middle = if scan.bytes_scanned == 0 {
        None
    } else {
        let root = finish(&mut deferred, &mut levels, &mut counters)?;
        Some(root)
    };
    let (left, tail) = split(&mut deferred, old_summary, start, true, &mut counters)?;
    let (_, right) = split_optional(&mut deferred, tail, delete_len, &mut counters)?;
    let prefix = concat_optional(&mut deferred, left, middle, &mut counters)?;
    let joined = concat_optional(&mut deferred, prefix, right, &mut counters)?;
    let mapping = match joined {
        Some(summary) => summary,
        None => emit_leaf(&mut deferred, Vec::new(), &mut counters)?,
    };
    let committed = deferred.commit(mapping)?;
    counters.nodes_created = add(persisted_nodes, committed)?;
    let next = FileStateV3 {
        logical_len: mapping.bytes,
        extent_count: mapping.extents,
        tree_level: mapping.level,
        profile_id: profile_id(),
        mapping_root: mapping.id,
    };
    counters.logical_len_after = Some(next.logical_len);
    let canonical = encode_file_state(next)?;
    let id = store.put(&canonical)?;
    Ok((FileStateRoot(id), counters))
}

```

## 7. A namespace maps names to stable inodes, then inodes to records

The filesystem root is a NamespaceRoot: it identifies the root directory inode and an inode-table root. A directory's tree maps byte-oriented names to `InodeId`s. The inode table maps each inode ID to an immutable inode record. That record references content and metadata separately. A regular file's content is a FileState; a directory's content is a DirectoryState; a symlink has its own target representation.

This indirection matters for hard links: multiple names can identify the same regular-file inode, so updating its record changes what all those links observe in the new snapshot. `namespace_ref_count` records namespace references; the root directory has its special zero-reference rule. Inode identity is allocated from a seed and serial, rather than being the hash of the file's current bytes.

`CanonicalName` and `CanonicalPath` preserve bytes and enforce validation; name ordering compares bytes. Directory, inode-table, and metadata modules each provide codecs, reads, validation, and structural updates. Portable mode/mtime metadata and auxiliary metadata values are represented separately from file content.

```bash
sed -n '7,21p' crates/layerfs-content/src/tree/inode/record.rs
```

```output
impl InodeId {
    pub fn allocate(store_id: [u8; 32], serial: u64) -> Self {
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"layerfs/inode-id/v1\0");
        hasher.update(&store_id);
        hasher.update(&serial.to_be_bytes());
        Self(*hasher.finalize().as_bytes())
    }

    pub fn from_slice(bytes: &[u8]) -> CoreResult<Self> {
        Ok(Self(ObjectId::from_bytes(bytes)?.to_bytes()))
    }

    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
```

```bash
sed -n '57,80p' crates/layerfs-content/src/tree/inode/record.rs
```

```output
        } else {
            self.namespace_ref_count >= 1
                && (self.kind == InodeKind::RegularFile || self.namespace_ref_count == 1)
        };
        if valid {
            Ok(())
        } else {
            Err(crate::CoreError::InvalidRecord("namespace ref count"))
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InodeTableRoot(pub ObjectId);

pub struct GeneratedInodeTable(pub(super) InodeTableRoot);

impl GeneratedInodeTable {
    pub const fn root(&self) -> InodeTableRoot {
        self.0
    }

    pub const fn into_root(self) -> InodeTableRoot {
        self.0
```

```bash
sed -n '11,33p' crates/layerfs-content/src/filesystem/root.rs
```

```output
    let root_inode = InodeId::allocate(seed, 0);
    let directory = empty_directory(store)?;
    let metadata = metadata(store, InodeKind::Directory, 0o755)?;
    let record = store.put(&encode_inode_record(InodeRecordV1 {
        kind: InodeKind::Directory,
        namespace_ref_count: 0,
        content_root: directory.0,
        metadata_root: metadata,
    })?)?;
    let table = inode_table_from_root(store, root_inode, record)?;
    store.put(&encode_namespace_root(NamespaceRootV1 {
        profile_id: profile_id(),
        root_directory_inode: root_inode,
        inode_table_root: table.0,
    })?)
}
```

## 8. Logical filesystem operations rebuild the affected roots

`filesystem::replace_range_with_metadata` is a complete example of composing the lower-level algorithms. It resolves the path through the namespace, rejects non-regular files, invokes the rope replacement, creates an updated inode record, and updates the inode table. It can preserve the existing metadata root or use a supplied one. The surrounding function emits a new namespace root and a `CandidateRoot` containing both parent and result IDs plus work counters.

Namespace insert/remove/rename and batch operations use the same approach with directory and inode-table updates. Since directory entries name stable inodes, a file-content change need not rewrite every directory ancestor merely to propagate a new file-content hash. Directory binding changes do modify the affected directory structures. Sorted batch algorithms in `tree/batch.rs` combine multiple changes and share untouched subtrees.

The content layer produces candidate roots; it does not decide which Branch should point to them. Publication is the Store's responsibility.

```bash
sed -n '92,153p' crates/layerfs-content/src/filesystem/apply.rs
```

```output
pub fn replace_range_with_metadata<S: ObjectStore, R: Read>(
    store: &mut S,
    root: ObjectId,
    path: &CanonicalPath,
    start: u64,
    delete_len: u64,
    replacement: R,
    metadata_root: Option<ObjectId>,
) -> CoreResult<CandidateRoot> {
    let mut counters = LogicalCounters::default();
    let resolved = resolve(store, root, path, &mut counters)?;
    if resolved.record.kind != InodeKind::RegularFile {
        return Err(CoreError::WrongLogicalRole);
    }
    let previous = store.set_file_payload_context(true);
    let result = replace(
        store,
        FileStateRoot(resolved.record.content_root),
        start,
        delete_len,
        replacement,
    );
    store.set_file_payload_context(previous);
    let (content, rope) = result?;
    counters.rope = rope;
    let NamespaceRootV1 {
        profile_id,
        root_directory_inode,
        inode_table_root,
    } = namespace(store, root)?;
    let record = store.put(&encode_inode_record(InodeRecordV1 {
        content_root: content.0,
        metadata_root: metadata_root.unwrap_or(resolved.record.metadata_root),
        ..resolved.record
    })?)?;
    let (table, inode) = inode_table_upsert(
        store,
        InodeTableRoot(inode_table_root),
        resolved.inode,
        record,
    )?;
    counters.inode_table.nodes_read = counters
        .inode_table
        .nodes_read
        .checked_add(inode.nodes_read)
        .ok_or(CoreError::LengthOverflow)?;
    counters.inode_table.nodes_created = counters
        .inode_table
        .nodes_created
        .checked_add(inode.nodes_created)
        .ok_or(CoreError::LengthOverflow)?;
    let canonical = encode_namespace_root(NamespaceRootV1 {
        profile_id,
        root_directory_inode,
        inode_table_root: table.0,
    })?;
    Ok(CandidateRoot {
        parent_root: root,
        root: store.put(&canonical)?,
        counters,
    })
}
```

## 9. SQLite stores object locations and history records

The current schema constant is 7, with explicit handling for the legacy schema-6 layout. Do not infer the active schema from test filenames such as `v4.rs` or the Cargo package version. The `objects` table indexes a canonical object ID and length into a pack, group, and record; `object_packs` owns the blob. Canonical object bytes are therefore not simply stored as one raw blob per object row.

History is a separate set of records. A LayerStack has a head Layer. Each Layer records its parent, root, and optional source Branch/Commit. A Branch records its base Layer and optional head Commit. A Commit records its root, parent Commit, and base Layer. SQL foreign keys and uniqueness indexes enforce the corresponding relationships. `workspace_stages` can retain an unpublished candidate root independently of the Branch head.

`LayerStackStore` shares `StoreDb` through an Arc. The database code has a connection mutex, an operation ticket gate, and a set of Branch leases. In-process leases coordinate shared Store instances; final publication still checks the expected database state.

```bash
sed -n '10,15p' crates/layerfs-layerstack-store/src/schema.rs
```

```output
pub const APPLICATION_ID: i64 = 0x4c46_534c;
pub const SCHEMA_VERSION: i64 = 7;
pub const LEGACY_SCHEMA_VERSION: i64 = 6;
// Creation policy is independent of supported existing schema-6 layouts.
pub const NEW_STORE_PAGE_SIZE_BYTES: i64 = 4096;
pub const SQLITE_PAGE_CACHE_KIB: i64 = 32 * 1024;
```

```bash
sed -n '68,79p' crates/layerfs-layerstack-store/src/schema.rs
```

```output
pub(crate) struct StoreDb(Arc<StoreInner>);

struct StoreInner {
    write_failed: std::sync::atomic::AtomicBool,
    format_version: i64,
    physical: crate::telemetry::PhysicalStorageCounters,
    connection: Mutex<Connection>,
    gate: Arc<TicketGate>,
    leases: Mutex<BTreeSet<BranchId>>,
    path: PathBuf,
}

```

```bash
sed -n '1,18p' crates/layerfs-layerstack-store/sql/schema/v7.sql
```

```output
PRAGMA application_id = 1279677260;
PRAGMA user_version = 7;

CREATE TABLE object_packs (
    pack_id INTEGER PRIMARY KEY CHECK (pack_id > 0),
    data BLOB NOT NULL
) STRICT;

CREATE TABLE objects (
    object_id BLOB NOT NULL PRIMARY KEY CHECK (length(object_id) = 32),
    canonical_length INTEGER NOT NULL
        CHECK (canonical_length > 0 AND canonical_length <= 16777216),
    pack_id INTEGER NOT NULL REFERENCES object_packs(pack_id),
    group_number INTEGER NOT NULL CHECK (group_number >= 0 AND group_number < 256),
    record_number INTEGER NOT NULL CHECK (record_number >= 0 AND record_number < 8191)
) STRICT, WITHOUT ROWID;

CREATE TABLE commits (
```

```bash
sed -n '86,124p' crates/layerfs-layerstack-store/src/records.rs
```

```output
    pub name: EntityName,
    pub head_layer_id: LayerId,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LayerRecord {
    pub id: LayerId,
    pub layer_stack_id: LayerStackId,
    pub parent_layer_id: Option<LayerId>,
    pub root_id: ObjectId,
    pub source_branch_id: Option<BranchId>,
    pub source_commit_id: Option<CommitId>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CommitRecord {
    pub id: CommitId,
    pub root_id: ObjectId,
    pub parent_commit_id: Option<CommitId>,
    pub base_layer_id: LayerId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BranchRecord {
    pub id: BranchId,
    pub layer_stack_id: LayerStackId,
    pub name: EntityName,
    pub base_layer_id: LayerId,
    pub head_commit_id: Option<CommitId>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WorkspaceStage {
    pub workspace_id: [u8; 16],
    pub branch_id: BranchId,
    pub root_id: ObjectId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
```

## 10. Physical packing preserves canonical identity

`objects.rs` bridges canonical construction and physical storage. `ObjectBuffer` overlays a source with deferred output; `DeferredObjectStore` supports bounded buffering/spilling. Checked admission establishes which generated objects are new or reused and sends finalized output to storage. These types avoid requiring every candidate byte to remain resident until final publication.

The physical implementation is divided into `objects/admission.rs` (representation selection and writing), `objects/pack.rs` (wire grammar), and `objects/read.rs` (location lookup, extraction, reconstruction, authentication). The native record grammar has Full and Prefix variants; a Prefix names a base object. Full compression and predecessor-assisted compression are physical alternatives under the same canonical ID. Optional predecessor discovery has work limits and can fall back, whereas an admitted dependency must be readable and valid.

On a demanded read, the Store reconstructs canonical bytes, checks their expected length, and authenticates their identity. Bounded decoding and dependency traversal are as important here as hashing. A snapshot root authenticates structure through its references as reads descend; reading only the root is not an exhaustive scan of all descendants.

```bash
sed -n '150,191p' crates/layerfs-layerstack-store/src/objects/pack.rs
```

```output
pub(super) const NATIVE_RAW_LIMIT: usize = 32_768;
pub(super) const NATIVE_FRAME_LIMIT: usize = 33_024;
pub(super) const NATIVE_ENCODE_WORKSPACE: usize = 1_048_576;
pub(super) const NATIVE_DECODE_WORKSPACE: usize = 262_144;

#[derive(Debug)]
pub(super) enum NativeRecord<'a> {
    Full {
        raw_length: usize,
        frame: &'a [u8],
    },
    Prefix {
        raw_length: usize,
        base: ObjectId,
        frame: &'a [u8],
    },
}

pub(super) fn native_record(bytes: &[u8]) -> Result<NativeRecord<'_>> {
    let raw_length = u32_at(bytes, 1)?;
    if raw_length > NATIVE_RAW_LIMIT {
        return Err(invalid());
    }
    let (base, start) = match bytes.first() {
        Some(0) => (None, 5),
        Some(1) => (
            Some(ObjectId::from_bytes(bytes.get(5..37).ok_or_else(invalid)?)?),
            37,
        ),
        _ => return Err(invalid()),
    };
    let frame = bytes.get(start..).ok_or_else(invalid)?;
    if frame.is_empty() || frame.len() > NATIVE_FRAME_LIMIT {
        return Err(invalid());
    }
    Ok(match base {
        None => NativeRecord::Full { raw_length, frame },
        Some(base) => NativeRecord::Prefix {
            raw_length,
            base,
            frame,
        },
```

```bash
sed -n '975,982p' crates/layerfs-layerstack-store/src/objects/read.rs
```

```output
fn authenticate(id: ObjectId, bytes: &[u8], length: usize) -> Result<()> {
    if bytes.len() != length {
        return Err(StoreError::Integrity("object canonical length"));
    }
    layerfs_content::authenticate_identity(bytes, id)?;
    super::note_read_batch_hash();
    Ok(())
}
```

## 11. Initialize a LayerStack, then fork by referencing history

Initialization starts by allocating a LayerStack ID and an inode-allocation seed. Empty initialization uses the namespace constructor we already saw. Directory initialization attempts the direct import pipeline, with a serial fallback. The importer constructs file objects and namespace structure; the admission path handles canonical reuse and physical storage. Only after a complete root is available does initialization publish the genesis Layer and LayerStack metadata.

Forking is much smaller: from a Layer it creates a Branch whose base is that Layer and whose head Commit is absent. From a Branch Commit it first proves that the Commit belongs to the requested Branch's history and that the base Layer belongs to the same LayerStack, then records those existing references in a new Branch row. It does not copy the file tree or payloads.

This is the source of zero-copy history branching. Creating a materialized projection later can still copy bytes to ordinary files; that cost is separate from creating the Branch record.

```bash
sed -n '38,66p' crates/layerfs-layerstack-store/src/layerstack.rs
```

```output
        ) = match source {
            LayerStackInitialization::Empty => {
                let built = empty_root(seed)?;
                let mut admission = CheckedOutputAdmission::new(&self.db)?;
                admission.admit(built.objects)?;
                let finished = admission.finish()?;
                (
                    built.root_id,
                    0,
                    0,
                    0,
                    finished.final_batch,
                    finished.receipt,
                    finished.statement_number,
                    None,
                )
            }
            LayerStackInitialization::Directory(path) => {
                if !path.is_dir() {
                    return Err(StoreError::InvalidInput("Layer initialization directory"));
                }
                let started = std::time::Instant::now();
                let mut attempted = SourceImportMetrics::default();
                let prepared = direct_initialize_root_directories_inner(
                    &self.db,
                    &path,
                    seed,
                    &mut attempted,
                )?;
```

```bash
sed -n '104,118p' crates/layerfs-layerstack-store/src/layerstack.rs
```

```output
            id: LayerId::derive(layer_stack_id, None, root_id),
            layer_stack_id,
            parent_layer_id: None,
            root_id,
            source_branch_id: None,
            source_commit_id: None,
        };
        let stack = LayerStackRecord {
            id: layer_stack_id,
            name: name.clone(),
            head_layer_id: layer.id,
        };
        let prepared = PreparedAdmission::prepare_missing(&self.db, final_batch)?;
        let mut name_insert_failed = false;
        let publication = prepared.publish(
```

```bash
sed -n '9,51p' crates/layerfs-layerstack-store/src/branch.rs
```

```output
    pub fn fork_branch(&self, name: EntityName, source: LocalForkSource) -> Result<BranchId> {
        let _operation = self.db.enter_operation()?;
        let incoming_id = BranchId::new();
        let branch = match source {
            LocalForkSource::Layer { layer_id } => {
                let layer = self.layer(layer_id)?.ok_or(StoreError::NotFound("Layer"))?;
                BranchRecord {
                    id: incoming_id,
                    layer_stack_id: layer.layer_stack_id,
                    name: name.clone(),
                    base_layer_id: layer.id,
                    head_commit_id: None,
                }
            }
            LocalForkSource::Branch {
                branch_id,
                commit_id,
            } => {
                let source = self
                    .branch(branch_id)?
                    .ok_or(StoreError::NotFound("Branch"))?;
                if !self.branch_contains_commit(branch_id, commit_id)? {
                    return Err(StoreError::InvalidInput("Commit outside Branch history"));
                }
                let commit = self
                    .commit(commit_id)?
                    .ok_or(StoreError::Integrity("Branch Commit"))?;
                let base = self
                    .layer(commit.base_layer_id)?
                    .ok_or(StoreError::Integrity("Commit base Layer"))?;
                if base.layer_stack_id != source.layer_stack_id {
                    return Err(StoreError::Integrity("Branch LayerStack ownership"));
                }
                BranchRecord {
                    id: incoming_id,
                    layer_stack_id: source.layer_stack_id,
                    name: name.clone(),
                    base_layer_id: commit.base_layer_id,
                    head_commit_id: Some(commit_id),
                }
            }
        };
        let mut connection = self.db.writer()?;
```

## 12. Create a workspace from a pinned snapshot

Workspace creation validates an absolute placement path, pins the Branch's current root/context, creates a runtime state directory and spool location, and initializes a Workspace from that snapshot. The Workspace remembers its expected head Commit, expected base Layer, base root, and reader. Those expected values will later prevent publishing over a head that moved.

The live tree starts with the root node and acquires more immutable namespace facts as needed. A WorkspaceWorker associates that state with lifecycle coordination, projection ownership, execution tracking, and identity information. Once attachment succeeds, the manager registers the session; failure paths clean up the temporary state.

Projection defaults are platform-sensitive: container placement or Linux defaults to FUSE, while other host placement defaults to materialization. The selected build must support the projection. A placement choice is not a second Store; all candidate construction and history publication still return to the original Store.

```bash
sed -n '402,446p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
    pub fn create_workspace_session(
        &self,
        request: CreateWorkspaceSession,
    ) -> WorkspaceResult<WorkspaceSession> {
        self.prune_retained()?;
        if !request.placement.root().is_absolute() || request.placement.root().parent().is_none() {
            return Err(WorkspaceError::InvalidPlacement);
        }
        let pinned = self.store.pin_branch(request.branch_id)?;
        let identity = crate::worker::WorkspaceIdentity {
            layer_stack_id: pinned.layer_stack.id,
            layer_stack_name: pinned.layer_stack.name.clone(),
            branch_name: pinned.branch.name.clone(),
        };
        let id = WorkspaceId::new();
        let state = self.runtime_root.join("workspaces").join(id.to_string());
        std::fs::create_dir_all(&state)?;
        let workspace = Workspace::from_snapshot(
            crate::cow_tree::WorkspaceSnapshot {
                store: self.store.clone(),
                workspace_id: id.bytes(),
                branch_id: request.branch_id,
                expected_head: pinned.branch.head_commit_id,
                expected_base: pinned.branch.base_layer_id,
                root: pinned.root,
                reader: pinned.reader,
            },
            &state.join("spool"),
            crate::ResourcePolicy::default(),
        )?;
        let projection = request.projection.unwrap_or({
            if matches!(
                request.placement,
                crate::WorkspacePlacement::Container { .. }
            ) || cfg!(target_os = "linux")
            {
                WorkspaceProjection::Fuse
            } else {
                WorkspaceProjection::Materialize
            }
        });
        let worker = Arc::new(WorkspaceWorker::new(
            id,
            request.clone(),
            projection,
```

```bash
sed -n '36,59p' crates/layerfs-workspace/src/cow_tree.rs
```

```output

pub struct Workspace {
    pub(crate) remote: Option<crate::live_backing::RemoteWorkspace>,
    pub(crate) live: layerfs_workspace_core::LiveWorkspace,
    pub(crate) store: LayerStackStore,
    pub(crate) workspace_id: [u8; 16],
    pub(crate) reader: SnapshotReader,
    pub(crate) branch_id: BranchId,
    pub(crate) expected_head: Option<CommitId>,
    pub(crate) expected_base: LayerId,
    pub(crate) base_root: layerfs_content::ObjectId,
    pub(crate) base_inodes: InodeTableRoot,
    pub(crate) directory_lookup_cache: layerfs_content::tree::directory::DirectoryLookupCache,
    pub(crate) spool: PathBuf,
    pub(crate) backing: crate::file_io::HostSpool,
    pub(crate) capture: crate::capture::CaptureState,
    pub(crate) state: WorkspaceState,
    pub(crate) presentation_failed: bool,
    pub(crate) resolution: Option<crate::reconcile::ResolutionState>,
    pub(crate) pending_checkpoint: Option<crate::changes::Checkpoint>,
    pub(crate) pending_stage: Option<layerfs_content::ObjectId>,
    pub(crate) pending_publication:
        Option<(layerfs_layerstack_store::CommitOutcome, LayerId, bool)>,
}
```

## 13. Mutable files are piece trees over immutable bytes and backing ranges

`layerfs-workspace-core` contains the portable live model used by native and execution-side adapters. A file is either a Base root/length or Edited state with an optional base and a persistent piece tree. DirectoryData similarly overlays a base directory with name-to-node changes; a missing node value represents a removed binding.

Pieces can refer to base file regions, retained spool regions, inline bytes, or logical zeros. The adapters own physical backing I/O; the core computes the state transformation. `prepare_write` performs no I/O and changes no live state. It validates the expected revision, range arithmetic, resulting pieces, and resource usage; an adapter acquires/writes backing and applies the prepared edit under the required ordering. Revalidation prevents installing a plan against a changed inode.

The write calculation distinguishes overwriting existing bytes from extending beyond EOF: an extension can add a zero gap. Nodes track links, open/reference pins, and revisions. Pins allow an unlinked file to remain alive while a handle still uses it. A ReadPlan retains an immutable description so physical reads can occur after live-state locks are released.

```bash
sed -n '68,101p' crates/layerfs-workspace-core/src/lib.rs
```

```output
pub enum FileData {
    Base {
        root: FileStateRoot,
        len: u64,
    },
    Edited {
        base: Option<(FileStateRoot, u64)>,
        spool_high_water: u64,
        pieces: crate::file_edit::PieceTree,
        edits: u32,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DirectoryData {
    pub base: Option<DirectoryStateRoot>,
    pub changes: BTreeMap<Vec<u8>, Option<NodeId>>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Node {
    pub revision: u64,
    pub canonical: Option<InodeId>,
    pub paths: BTreeSet<String>,
    pub mode: u32,
    pub links: u32,
    pub pins: u32,
    pub mtime_seconds: i64,
    pub mtime_nanoseconds: u32,
    pub data: Data,
}

impl Node {
    pub fn attr(&self, node: NodeId) -> Attr {
```

```bash
sed -n '49,66p' crates/layerfs-workspace-core/src/file_edit.rs
```

```output
    /// Does no I/O and changes no live state. The adapter must hold affected-inode
    /// ordering and resource admission across preparation, acquisition and apply.
    pub fn prepare_write(
        &self,
        node: NodeId,
        offset: u64,
        bytes: usize,
        backing: Option<SpoolSlice>,
    ) -> Result<PreparedFileEdit> {
        self.next_generation()?;
        let expected = self.nodes.get(&node).ok_or(Error::NotFound("node"))?;
        expected
            .revision
            .checked_add(1)
            .ok_or(Error::Integrity("inode revision"))?;
        if bytes == 0 {
            return Err(Error::InvalidInput("empty prepared write"));
        }
```

```bash
sed -n '103,131p' crates/layerfs-workspace-core/src/file_edit.rs
```

```output
        let next = old.replace(start, delete_len, gap.into_iter().chain([piece]))?;
        let prepared = PreparedFileEdit {
            node,
            expected_revision: expected.revision,
            before: before.clone(),
            next: FileData::Edited {
                base,
                spool_high_water: high_water
                    .checked_add(appended)
                    .ok_or(Error::InvalidInput("workspace spool limit"))?,
                pieces: next,
                edits: next_edit(edits)?,
            },
            appended,
            bytes,
            generations: 1,
        };
        self.write_resources(&prepared)?;
        Ok(prepared)
    }

    pub fn prepare_truncate(&self, node: NodeId, size: u64) -> Result<Option<PreparedFileEdit>> {
        let expected = self.nodes.get(&node).ok_or(Error::NotFound("node"))?;
        let Data::File(before) = &expected.data else {
            return Err(Error::InvalidInput("file"));
        };
        if expected.attr(node).size == size {
            return Ok(None);
        }
```

## 14. Resource accounting is part of the mutation contract

Default workspace policy bounds backing spool charge at 1 GiB and final-delta memory at 8 MiB. File-edit limits separately bound edit count, piece count, inline data, logical result size, and zero expansion. These are distinct resources: a small logical edit may retain old backing ranges because outstanding readers still own them.

The persistent piece model permits old read plans and new file versions to coexist. Releasing the final reference is what makes old backing reclaimable. This is why truncating a file does not necessarily release its entire historical spool charge immediately. Candidate workers likewise divide shared allowances instead of giving each worker an independent full budget.

When tracing a NoSpace or invalid-resource result, follow admission and retained backing lifetimes as well as current file length. The budget is about physical/temporary work and ownership, not merely the bytes visible at the latest path.

```bash
sed -n '3,34p' crates/layerfs-workspace-core/src/limits.rs
```

```output
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ResourcePolicy {
    pub max_spool_bytes: u64,
    pub max_final_delta_memory_bytes: u64,
}

impl Default for ResourcePolicy {
    fn default() -> Self {
        Self {
            max_spool_bytes: 1024 * 1024 * 1024,
            max_final_delta_memory_bytes: 8 * 1024 * 1024,
        }
    }
}

impl ResourcePolicy {
    pub fn check(self, spool_bytes: u64) -> Result<()> {
        if spool_bytes <= self.max_spool_bytes {
            Ok(())
        } else {
            Err(Error::InvalidInput("workspace spool limit"))
        }
    }

    pub fn check_final_delta(self, memory_bytes: u64) -> Result<()> {
        if memory_bytes <= self.max_final_delta_memory_bytes {
            Ok(())
        } else {
            Err(Error::InvalidInput("workspace final-delta limit"))
        }
    }
}
```

```bash
sed -n '5,12p' crates/layerfs-workspace-core/src/file_edit.rs
```

```output
pub const MAX_EDITS_PER_FILE: u32 = 4_096;
pub const MAX_PIECES_PER_FILE: usize = 8_193;
pub const MAX_INLINE_PER_EDIT: usize = 1024 * 1024;
pub const MAX_INLINE_PER_WORKSPACE: u64 = 8 * 1024 * 1024;
pub const MAX_PIECE_ALLOCATION: u64 = 2 * 1024 * 1024;
pub const MAX_RESULT_BYTES: u64 = 1024 * 1024 * 1024 * 1024;
pub const MAX_LOGICAL_ZERO_BYTES: u64 = 1024 * 1024 * 1024;
pub const MAX_PREDICTED_ZERO_EXTENTS: u64 = 131_072;
```

## 15. Project the live model into a real filesystem

`projection::attach` selects a host materialization, a host FUSE mount, or a Docker FUSE projection. Materialization walks the source into an ordinary directory, reconstructing regular files, directories, symlinks, hard links, and supported metadata. The destination must be empty. Before commit, capture compares/reads the ordinary tree and translates its effects back into workspace state. The projection module has a localized capture path and a broader fallback.

FUSE instead serves operations against the live workspace model. `FilesystemPort` is the adapter-facing interface, `filesystem.rs` implements callbacks, `inode_table.rs` and `handles.rs` manage kernel-facing identity/lifetimes, and `host_mount.rs` owns the mount. Kernel reference accounting matters particularly for lookup and READDIRPLUS, because entries accepted by the kernel remain referenced until forgotten.

FUSE writes already update live state, so its capture path does not need to rescan a materialized directory. Commit still needs the pause/quiesce boundary: live callbacks and dirty kernel state must be coordinated with the snapshot cut.

```bash
sed -n '16,30p' crates/layerfs-workspace/src/projection.rs
```

```output
    Materialized(PathBuf),
    Docker(Box<crate::docker::DockerProjection>),
    #[cfg(all(target_os = "linux", feature = "host-fuse"))]
    Fuse(layerfs_fuse::HostMount),
}

pub(crate) fn attach(
    worker: &Arc<WorkspaceWorker>,
    daemon: Option<&crate::daemon::DaemonOwner>,
) -> WorkspaceResult<ProjectionHandle> {
    if let WorkspacePlacement::Container { container_id, root } = &worker.request.placement {
        if worker.projection != crate::WorkspaceProjection::Fuse {
            return Err(WorkspaceError::InvalidPlacement);
        }
        let (remote, runtime) = {
```

```bash
sed -n '60,84p' crates/layerfs-workspace/src/projection.rs
```

```output
    };
    match worker.projection {
        crate::WorkspaceProjection::Materialize => {
            let source = MaterializedView(Arc::downgrade(worker));
            materialize_atomic(&source, root)?;
            Ok(ProjectionHandle::Materialized(root.clone()))
        }
        crate::WorkspaceProjection::Fuse => {
            #[cfg(all(target_os = "linux", feature = "host-fuse"))]
            {
                std::fs::create_dir_all(root)?;
                let remote = {
                    let mut workspace = worker
                        .workspace
                        .lock()
                        .map_err(|_| WorkspaceError::WorkspaceBusy)?;
                    let remote = crate::live_backing::RemoteWorkspace::start_local(&workspace)?;
                    workspace.remote = Some(remote.clone());
                    *worker
                        .remote
                        .lock()
                        .map_err(|_| WorkspaceError::WorkspaceBusy)? = Some(remote.clone());
                    remote
                };
                let owner = Arc::new(
```

```bash
sed -n '8,17p' crates/layerfs-materialization/src/materialize.rs
```

```output
pub fn materialize(source: &dyn MaterializationSource, destination: &Path) -> Result<()> {
    std::fs::create_dir_all(destination)?;
    if std::fs::read_dir(destination)?.next().is_some() {
        return Err(MaterializationError::Invalid("materialization destination"));
    }
    let root = source.root();
    let mut inodes = HashMap::new();
    walk(source, root.node, destination, &mut inodes)?;
    set_metadata(destination, root)
}
```

```bash
sed -n '32,62p' crates/layerfs-materialization/src/materialize.rs
```

```output
fn walk(
    source: &dyn MaterializationSource,
    node: NodeId,
    destination: &Path,
    inodes: &mut HashMap<NodeId, PathBuf>,
) -> Result<()> {
    for entry in source.entries(node)? {
        let path = destination.join(std::ffi::OsStr::from_bytes(&entry.name));
        if let Some(first) = inodes.get(&entry.attr.node) {
            std::fs::hard_link(first, &path)?;
            continue;
        }
        match entry.attr.kind {
            Kind::Directory => {
                std::fs::create_dir(&path)?;
                walk(source, entry.attr.node, &path, inodes)?;
                set_metadata(&path, entry.attr)?;
            }
            Kind::File => {
                let mut file = std::fs::File::create(&path)?;
                source.read(entry.attr.node, &mut file)?;
                set_metadata(&path, entry.attr)?;
            }
            Kind::Symlink => {
                let target = source.readlink(entry.attr.node)?;
                symlink(std::ffi::OsStr::from_bytes(&target), &path)?;
            }
        }
        inodes.insert(entry.attr.node, path);
    }
    Ok(())
```

## 16. Execution-side state and host storage communicate through explicit protocols

`LiveOwner` owns execution-side live state, per-node ordering, namespace coordination, operation gates, kernel references, read caches, and backing-range references. Its module comment states the division directly: physical storage and canonical construction remain on the host. `live_runtime.rs` supplies scheduling and operation cuts; `live_transport.rs` and `live_wire.rs` carry backing and control requests; workspace `live_backing.rs` receives frozen facts and later installs checkpoints.

The container daemon is a separate mount/process control service. Its protocol has framed Exec, Stop, stdout/stderr, Exit, Mount, Close, and resource-sampling messages, with explicit argument and frame bounds. The client/daemon implementation authenticates ownership using capability and connection-bound state. A ContainerBinding carries the daemon owner into the workspace manager.

`container.rs` controls container creation/start/stop and limits, `docker_engine.rs` handles engine interaction, and the runtime image is defined in `containers/layerfs-fuse/Dockerfile`. Container projection keeps the SQLite Store on the host. The older proxy client/host modules also implement a FilesystemPort transport; read the live-owner path to understand the current shared live-state implementation.

```bash
sed -n '1,14p' crates/layerfs-fuse/src/live_owner.rs
```

```output
//! Execution-side live state. Physical storage and canonical construction stay on the host.
use crate::live_runtime::{LiveRuntime, OperationGate, Scheduler};
use crate::live_transport::BackingConnection;
use crate::live_wire::{self as wire, Input};
use crate::port::{DirectoryPage, KernelEntry, KernelReferences};
use crate::{Attr, FilesystemPort, Kind, NodeId, PortError, PortResult, ROOT};
use layerfs_workspace_core::backing::{BackingId, BackingRef};
use layerfs_workspace_core::file_edit::{Piece, SpoolSlice};
use layerfs_workspace_core::namespace::{AcquiredInode, NameLookup, ResolvedName};
use layerfs_workspace_core::{Data, LiveWorkspace, ReadPlan, ReadSource, ResourcePolicy};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;
```

```bash
sed -n '28,60p' crates/layerfs-fuse/src/live_owner.rs
```

```output
    scheduler: Scheduler,
    read_scope: u64,
    state: Mutex<LiveWorkspace>,
    kernel_refs: Mutex<KernelRefState>,
    active_prefill: Mutex<Option<(NodeId, Arc<PrefillCompletionState>)>>,
    head: Mutex<Option<[u8; 33]>>,
    backing: BackingConnection,
    local_facts: Option<Arc<dyn Fn(LocalFacts) -> PortResult<()> + Send + Sync>>,
    // Only physical append allocation is ordered across files. No state lock
    // is retained while a physical reservation or append waits.
    append: tokio::sync::Mutex<Option<AppendWindow>>,
    failed: AtomicBool,
    closing: AtomicBool,
    cached: Mutex<std::collections::BTreeSet<NodeId>>,
    facts_sync: tokio::sync::Mutex<Option<(layerfs_content::ObjectId, u64)>>,
    ordering: Mutex<HashMap<NodeId, Arc<tokio::sync::Mutex<()>>>>,
    namespace: tokio::sync::Mutex<()>,
    directories: Mutex<HashMap<NodeId, DirectoryCookies>>,
    ranges: Mutex<HashMap<BackingId, BackingRef>>,
    edit: Mutex<Option<PendingSplices>>,
    kernel_edit: Mutex<Option<Arc<KernelEdit>>>,
    gate: OperationGate,
    writes: crate::write_metrics::AtomicFuseWriteMetrics,
    reads: crate::write_metrics::AtomicFuseReadMetrics,
    #[cfg(all(target_os = "linux", any(feature = "host", feature = "proxy")))]
    notifier: std::sync::OnceLock<fuser::Notifier>,
    #[cfg(target_os = "linux")]
    kernel_root: Mutex<Option<Arc<std::fs::File>>>,
    cut: Mutex<Option<crate::live_runtime::OperationCut>>,
    install: Mutex<Option<PendingCheckpoint>>,
}

impl Drop for Owner {
```

```bash
sed -n '3,38p' crates/layerfs-daemon/src/protocol.rs
```

```output
pub const SOCKET_PATH: &str = "/run/layerfs/daemon.sock";
pub const CAPABILITY_PATH: &str = "/run/layerfs/capability";
pub const WORKSPACE_ROOT: &str = "/workspace";
pub const MAGIC: [u8; 8] = *b"LFSDAEM3";
pub const VERSION: u16 = 1;
pub const MAX_CONTROL: usize = 1024 * 1024;
pub const MAX_OUTPUT: usize = 64 * 1024;
pub const MAX_ARG: usize = 128 * 1024;
pub const MAX_ARGS: usize = 4096;

pub const SERVER_HELLO_BYTES: usize = 8 + 2 + 16 + 32;
pub const CLIENT_AUTH_BYTES: usize = 32 + 32;
pub const AUTH_OK_BYTES: usize = 16 + 32;
pub const BOUND_AUTH_BYTES: usize = 32 + 32;
pub const BOUND_OK_BYTES: usize = 32;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum Kind {
    Exec = 1,
    Stop = 2,
    Started = 3,
    Stdout = 4,
    Stderr = 5,
    Exit = 6,
    Error = 7,
    Mount = 8,
    WorkspaceReady = 9,
    Close = 10,
    WorkspaceClosed = 11,
    ResourceSampleStart = 12,
    ResourceSampleStarted = 13,
    ResourceSampleFinish = 14,
    ResourceSample = 15,
    ResourceSampleClock = 16,
}
```

## 17. Run a process and consume bounded output

`Workspaces::exec` and `shell` enter the spawn path with a session and argv. Execution state can own a host child, Docker execution, or daemon execution transport. It tracks termination, output, completion, receipts, and byte counts separately from the Workspace. Each exec starts a fresh process; a shell is itself an execution, not a persistent environment implicitly inherited by later exec calls.

Output is exposed through `OutputReader::read(after, follow)`. Pages carry sequence-numbered chunks, the next cursor, truncation status, exit status, and an optional receipt. `OutputLog` retains a bounded 1 MiB tail, persists frames, and wakes following readers with a condition variable. Consumers must respect `truncated` instead of assuming output is an unlimited audit log.

Active executions and writers participate in workspace lifecycle coordination. Running a command does not publish a Commit. The caller explicitly asks to commit after the intended filesystem effects have completed.

```bash
sed -n '14,33p' crates/layerfs-workspace/src/execution.rs
```

```output
pub(crate) struct Execution {
    id: ExecutionId,
    session_id: WorkspaceId,
    process: Mutex<Option<ExecutionProcess>>,
    termination: Termination,
    output: Arc<OutputLog>,
    receipt: Mutex<Option<ExecutionReceipt>>,
    completed_at: Mutex<Option<std::time::SystemTime>>,
    stopped: AtomicBool,
    stdout_bytes: AtomicU64,
    stderr_bytes: AtomicU64,
}

enum ExecutionProcess {
    Child(Child),
    #[cfg(unix)]
    Docker(crate::docker_engine::DockerExec),
    #[cfg(unix)]
    Daemon(crate::daemon::DaemonExec),
}
```

```bash
sed -n '104,123p' crates/layerfs-workspace/src/execution.rs
```

```output
        session_id: WorkspaceId,
        argv: NonEmpty<Vec<OsString>>,
    ) -> WorkspaceResult<WorkspaceExecution> {
        self.spawn(session_id, argv.as_slice(), false)
    }

    pub fn shell(&self, session_id: WorkspaceId) -> WorkspaceResult<WorkspaceExecution> {
        let worker = self.worker(session_id)?;
        let argv = match &worker.request.placement {
            WorkspacePlacement::Host { .. } => {
                vec![std::env::var_os("SHELL").unwrap_or_else(|| OsString::from("/bin/sh"))]
            }
            WorkspacePlacement::Container { .. } => vec![OsString::from("/bin/sh")],
        };
        self.spawn(session_id, &argv, true)
    }

    pub fn stop(&self, execution_id: ExecutionId) -> WorkspaceResult<()> {
        let execution = self.execution(execution_id)?;
        if execution
```

```bash
sed -n '9,32p' crates/layerfs-workspace/src/output.rs
```

```output

pub struct OutputReader {
    log: Arc<OutputLog>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutputPage {
    pub chunks: Vec<OutputChunk>,
    pub next_sequence: u64,
    pub truncated: bool,
    pub exited: bool,
    pub receipt: Option<ExecutionReceipt>,
}

impl OutputReader {
    pub(crate) fn new(log: Arc<OutputLog>) -> Self {
        Self { log }
    }

    pub fn read(&self, after: u64, follow: bool) -> WorkspaceResult<OutputPage> {
        self.log.read(after, follow)
    }
}

```

```bash
sed -n '77,99p' crates/layerfs-workspace/src/output.rs
```

```output
            .state
            .lock()
            .map_err(|_| WorkspaceError::WorkspaceBusy)?;
        let truncated = bytes.len() > OUTPUT_TAIL_BYTES;
        let bytes = &bytes[bytes.len().saturating_sub(OUTPUT_TAIL_BYTES)..];
        let chunk = OutputChunk {
            sequence: state.next_sequence,
            stream,
            bytes: bytes.to_vec(),
        };
        let sequence = chunk.sequence;
        state.next_sequence += 1;
        state.bytes += chunk.bytes.len();
        append_frame(&self.path, &chunk)?;
        state.chunks.push_back(chunk);
        if truncated {
            state.truncated_through = Some(sequence);
        }
        let mut rewrite = false;
        while state.bytes > OUTPUT_TAIL_BYTES {
            let Some(removed) = state.chunks.pop_front() else {
                break;
            };
```

## 18. Begin commit by freezing a coherent view

`commit_workspace_session_with_status` serializes lifecycle operations for the worker. The local path checks for active executions; the projection is paused; writer/callback activity is quiesced. Busy is an explicit result. The remote path uses its projection/owner coordination to establish the operation cut rather than relying only on the host's execution check.

Once quiesced, the method captures a materialized view if needed and asks Workspace to commit. A retained stage or pending publication suppresses fresh capture: retry must finish the previously prepared operation, not accidentally replace it with a newly observed directory state.

Within `Workspace::commit`, unresolved reconciliation conflicts block publication. For ordinary commits it restores the frozen expected Branch context, obtains the mutation generation, and either reuses the base root for generation zero or builds a candidate. The result is still a candidate plus admission state at this point. The mounted view has not become durable history merely because writes were acknowledged or the operation was paused.

```bash
sed -n '547,590p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
            return Err(error);
        }
        let started = Instant::now();
        let quiesced = if remote.is_some() {
            worker.quiesce()
        } else {
            worker.wait_for_writers().and_then(|()| worker.quiesce())
        };
        layerfs_layerstack_store::note_workspace_commit_phase(
            WorkspaceCommitPhase::Quiesce,
            elapsed_ns(started),
        );
        let _quiesced = match quiesced {
            Ok(quiesced) => quiesced,
            Err(WorkspaceError::WorkspaceBusy) => {
                if let Err(error) = crate::projection::resume(&worker) {
                    let _ = crate::projection::end(&worker);
                    worker
                        .workspace
                        .lock()
                        .map_err(|_| WorkspaceError::WorkspaceBusy)?
                        .presentation_failed = true;
                    return Err(error);
                }
                return Ok(WorkspaceCommitStatus {
                    result: WorkspaceCommitResult::Busy,
                    presentation_failed: false,
                });
            }
            Err(error) => {
                crate::projection::resume(&worker)?;
                return Err(error);
            }
        };
        let result = (|| {
            let completion_pending = {
                let workspace = worker
                    .workspace
                    .lock()
                    .map_err(|_| WorkspaceError::WorkspaceBusy)?;
                workspace.pending_stage.is_some() || workspace.pending_publication.is_some()
            };
            if !completion_pending {
                let started = Instant::now();
```

```bash
sed -n '591,624p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
                let captured = crate::projection::capture(&worker);
                layerfs_layerstack_store::note_workspace_commit_phase(
                    WorkspaceCommitPhase::Capture,
                    elapsed_ns(started),
                );
                captured?;
            }
            let mut workspace = worker
                .workspace
                .lock()
                .map_err(|_| WorkspaceError::WorkspaceBusy)?;
            workspace.note_commit_edit_state()?;
            let previous_head = workspace.expected_head;
            let committed = match workspace.commit() {
                Ok((outcome, transition)) => Ok((
                    WorkspaceCommitResult::from_outcome(outcome, previous_head),
                    transition,
                )),
                Err(error) => WorkspaceError::from_commit(error)
                    .map(|result| (result, CommitTransition::Checkpointed)),
            };
            let observations = workspace.reader.read_metrics_snapshot().and_then(|after| {
                layerfs_layerstack_store::note_workspace_commit_reads(commit_read_before, after)
            });
            match (committed, observations) {
                (Ok((result @ WorkspaceCommitResult::Created { .. }, _)), Err(_))
                | (Ok((result @ WorkspaceCommitResult::UpToDate { .. }, _)), Err(_)) => {
                    workspace.presentation_failed = true;
                    Ok((result, CommitTransition::InstallationFailed))
                }
                (result, Ok(())) => result,
                (_, Err(error)) => Err(error.into()),
            }
        })();
```

## 19. Build only the changed namespace and file structures

`build_candidate` enters the frontier builder. Directory overlays already describe final binding changes, so the implementation can preserve untouched subtrees—including a relocated directory—without materializing complete before/after namespace manifests. Local state provides frozen changes directly; remote state supplies frozen facts from the backing owner.

CandidateInputs prepares eligible file tasks and caps producers by CPU availability, task count, and shared journal budget, with at most eight workers. Commit-purpose construction streams selected objects into Store admission; Preview-purpose construction keeps a private candidate path. File results are then combined with dirty directory/inode changes, reference-count adjustments, metadata, and a new namespace root. Journals and spillable structures bound intermediate state.

For an edited existing file, `mutate_existing_file` walks final pieces. Base pieces advance an old-file cursor; non-base pieces accumulate replacement regions. The builder emits `FileMutationBatch::replace` calls only where needed and compares suitable equal-length regions to avoid storing net-zero edits. New files or unsupported incremental correspondence take the full construction path. Final file lengths are checked before returning BuiltRoot and checkpoint information.

```bash
sed -n '344,357p' crates/layerfs-workspace/src/changes.rs
```

```output
    // Directory overlays already are the final binding delta. Applying their inode
    // edges directly preserves untouched subtrees, including a renamed directory,
    // without building either complete namespace manifest.
    fn build_frontier_candidate(&mut self, purpose: CandidatePurpose) -> Result<PreparedCommit> {
        let workers = std::thread::available_parallelism()
            .map(std::num::NonZeroUsize::get)
            .unwrap_or(1)
            .min(8);
        // CandidateInputs further caps workers by eligible tasks and partitions
        // the existing aggregate journal, candidate and spill allowances.
        self.build_frontier_candidate_with_workers(purpose, workers)
    }

    fn build_frontier_candidate_with_workers(
```

```bash
sed -n '615,636p' crates/layerfs-workspace/src/changes.rs
```

```output
                })
            };
        let finish = |worker: FileResultWriter| worker.finish();
        let (workers, admission) = match purpose {
            CandidatePurpose::Commit => {
                let (workers, admission) = self.store.construct_workspace_files(
                    self.workspace_id,
                    workers,
                    plan.count,
                    tasks,
                    initialize,
                    step,
                    finish,
                )?;
                (workers, Some(admission))
            }
            CandidatePurpose::Preview => (
                objects.construct_files(workers, plan.count, tasks, initialize, step, finish)?,
                None,
            ),
        };
        let mut files = FileResults::new(plan, workers, io_bytes / 2)?;
```

```bash
sed -n '1547,1585p' crates/layerfs-workspace/src/changes.rs
```

```output
        .logical_len;
        let mut base_cursor = 0_u64;
        let mut final_cursor = 0_u64;
        let mut replacement_len = 0_u64;
        for piece in pieces {
            match piece {
                crate::file_edit::Piece::Base { root, offset, len } => {
                    if root != file_root || offset < base_cursor {
                        return Err(StorageError::Integrity("Workspace base piece order"));
                    }
                    let delete_len = offset - base_cursor;
                    if (delete_len != 0 || replacement_len != 0)
                        && (delete_len != replacement_len
                            || final_cursor != base_cursor
                            || !self.workspace_range_matches_base(
                                file_root,
                                final_cursor,
                                final_cursor + replacement_len,
                            )?)
                    {
                        batch.replace(
                            final_cursor,
                            delete_len,
                            WorkspaceRangeReader::new(self, final_cursor, replacement_len)?,
                        )?;
                        changed = true;
                    }
                    final_cursor += replacement_len + len;
                    replacement_len = 0;
                    base_cursor = offset + len;
                }
                piece => {
                    replacement_len = replacement_len
                        .checked_add(piece.len())
                        .ok_or(StorageError::InvalidInput("file length"))?
                }
            }
        }
        let delete_len = original_len
```

## 20. Admit objects, retain a stage, then publish the Branch conditionally

This is the central storage transition. `commit_workspace_candidate` first verifies that admission belongs to this workspace and Store instance. It decides whether root and base are unchanged, admits remaining objects, validates candidate accounting, and records a workspace stage naming the complete candidate root. Staging and object admission precede the final history transaction.

Inside an immediate SQLite transaction, it reloads the stage and Branch snapshot. Both expected head Commit and base Layer must still match, and the expected root/LayerStack must be consistent. A mismatch does not overwrite another publisher. For a changed state it derives the Commit ID from root, parent, and base, inserts or verifies the immutable Commit, conditionally advances the Branch, and removes the stage. An unchanged state removes the stage and returns UpToDate without a new Commit.

The SQL predicate makes the expected-state check concrete. Object presence and public reachability are different facts: bytes can already be admitted while the Branch still points to its old Commit. The retained stage lets the workspace handle failed final publication explicitly.

```bash
sed -n '490,524p' crates/layerfs-layerstack-store/src/workspace.rs
```

```output
        let publication_started = Instant::now();
        let begin_started = Instant::now();
        let mut connection = self.db.writer()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let begin_ns = elapsed_ns(begin_started);
        let actual_stage = workspace_stage_from_connection(&transaction, workspace_id)?
            .ok_or(StoreError::Integrity("Workspace stage missing"))?;
        if actual_stage != stage {
            return Err(StoreError::Integrity("Workspace stage changed"));
        }
        let (current, current_root) =
            workspace_snapshot_from_connection(&transaction, expected.id)?;
        if current.head_commit_id != expected.head_commit_id
            || current.base_layer_id != expected.base_layer_id
        {
            return Err(StoreError::CommitHeadMoved {
                expected: expected.head_commit_id,
                actual: current.head_commit_id,
            });
        }
        if current.layer_stack_id != expected.layer_stack_id || current_root != expected_root {
            return Err(StoreError::Integrity("Workspace publication source"));
        }

        let metadata_started = Instant::now();
        let outcome = if up_to_date {
            delete_workspace_stage(&transaction, stage)?;
            statement_number += 1;
            crate::schema::fail_transaction_statement(statement_number)?;
            CommitOutcome::UpToDate {
                root_id: expected_root,
            }
        } else {
            let commit = CommitRecord {
                id: CommitId::derive(built.root_id, expected.head_commit_id, new_base_layer_id),
```

```bash
sed -n '533,540p' crates/layerfs-layerstack-store/src/workspace.rs
```

```output
            if transaction.execute(
                crate::statements::workspace::INSERT_COMMIT,
                rusqlite::params![
                    commit.id.as_slice(),
                    commit.root_id.as_bytes().as_slice(),
                    commit.parent_commit_id.map(|id| id.to_bytes().to_vec()),
                    commit.base_layer_id.as_slice(),
                ],
```

```bash
cat crates/layerfs-layerstack-store/sql/workspace/advance_branch.sql
```

```output
-- family: workspace
-- name: advance_branch
-- parameters: ?1 BranchId, ?2 new CommitId, ?3 expected CommitId, ?4 new base LayerId, ?5 expected base LayerId
-- affected rows: one CAS winner, zero if head/base moved
UPDATE branches
SET head_commit_id = ?2, base_layer_id = ?4
WHERE branch_id = ?1
  AND head_commit_id IS ?3
  AND base_layer_id = ?5;
```

## 21. A published Commit and a healthy projection are separate outcomes

After Store publication, the workspace installs checkpoint facts, retires superseded backing when ownership allows, and resumes or refreshes the projection. This phase can fail after the Branch update has succeeded. The status API preserves the successful Created/UpToDate result while reporting `presentation_failed: true`; retrying must not silently create a duplicate Commit to repair a mount.

`pending_stage` tracks a complete unpublished candidate. `pending_publication` tracks publication whose workspace transition still needs completion. `recover_workspace_presentation` provides the explicit recovery path. The lifecycle code also freezes uncertain completion state when it cannot reliably inspect the retained stage.

SQLite transaction atomicity in a live process must not be confused with crash/power-loss durability. The connection configuration uses MEMORY journaling and synchronous OFF. That concrete configuration explains the preview durability boundary; the code's successful commit result should not be read as a power-loss guarantee.

```bash
sed -n '664,689p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
                }
            },
            _ => {
                let started = Instant::now();
                let resumed = crate::projection::resume(&worker);
                layerfs_layerstack_store::note_workspace_commit_phase(
                    WorkspaceCommitPhase::Resume,
                    elapsed_ns(started),
                );
                resumed
            }
        };
        if let Err(error) = presentation {
            let _ = crate::projection::end(&worker);
            worker
                .workspace
                .lock()
                .map_err(|_| WorkspaceError::WorkspaceBusy)?
                .presentation_failed = true;
            return match result {
                Ok((result @ WorkspaceCommitResult::Created { .. }, _))
                | Ok((result @ WorkspaceCommitResult::UpToDate { .. }, _)) => {
                    Ok(WorkspaceCommitStatus {
                        result,
                        presentation_failed: true,
                    })
```

```bash
sed -n '367,384p' crates/layerfs-layerstack-store/src/schema.rs
```

```output

fn configure_connection(connection: &Connection) -> Result<()> {
    if rusqlite::version_number() < 3_037_000 {
        return Err(StoreError::Integrity("SQLite STRICT support"));
    }
    connection.pragma_update(None, "foreign_keys", true)?;
    connection.pragma_update(None, "journal_mode", "MEMORY")?;
    let journal: String = connection.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
    if !journal.eq_ignore_ascii_case("memory") {
        return Err(StoreError::WrongStoreSchema);
    }
    connection.pragma_update(None, "synchronous", "OFF")?;
    connection.pragma_update(None, "temp_store", "MEMORY")?;
    connection.pragma_update(None, "cache_size", -SQLITE_PAGE_CACHE_KIB)?;
    connection.pragma_update(None, "cache_spill", "OFF")?;
    connection.pragma_update(None, "mmap_size", 0_i64)?;
    connection.pragma_update(None, "threads", 0_i64)?;
    connection.pragma_update(None, "locking_mode", "EXCLUSIVE")?;
```

## 22. Reconcile a moved LayerStack and promote the result with Add

Branch commits and LayerStack promotion are separate timelines. `add_layer` loads the Branch head and checks whether it has already been promoted, whether its base still matches the LayerStack head, and whether its root actually differs. It returns UpToDate, HeadMoved, or NoChanges for those cases. Otherwise it creates a Layer referring to the existing Commit root and conditionally advances the LayerStack head in a transaction; it does not rebuild the file payloads.

When a Branch must be reconciled with a newer Layer, the content layer uses three-root logic: if the destination equals the old source value, take the source change; if both sides already agree, keep that value; otherwise report a collision. Higher-level reconciliation classifies content/type/directory/hard-link conflicts and exposes affected paths. Workspace conflict choices can select Branch, Layer, or WorkingTree, and intersecting later edits can invalidate prior resolutions.

Resolving conflicts produces a candidate that can be committed against checked history context. Add itself is not an unconditional merge or forced overwrite of a newer LayerStack head.

```bash
sed -n '27,45p' crates/layerfs-content/src/filesystem/reconcile.rs
```

```output
/// Applies the exact three-root rule for one inode-table change. Root-level
/// traversal remains streaming in `diff`; callers never need a complete inode
/// inventory to classify a change.
pub fn reconcile_inode_change(
    source: InodeTableDiff,
    destination: Option<ObjectId>,
) -> CoreResult<std::result::Result<Option<ObjectId>, ReconcileCollision>> {
    if destination == source.before {
        Ok(Ok(source.after))
    } else if destination == source.after {
        Ok(Ok(destination))
    } else {
        Ok(Err(ReconcileCollision {
            inode: source.inode,
            source: source.after,
            destination,
        }))
    }
}
```

```bash
sed -n '208,239p' crates/layerfs-layerstack-store/src/layerstack.rs
```

```output
    pub fn add_layer(&self, branch_id: BranchId) -> Result<AddLayerResult> {
        let _operation = self.db.enter_operation()?;
        let snapshot = self.load_add_snapshot(branch_id)?;
        if let Some(layer_id) = snapshot.existing_layer_id {
            return Ok(AddLayerResult::UpToDate { layer_id });
        }
        if snapshot.commit_base_layer_id != snapshot.branch_base_layer_id
            || snapshot.layer_stack_head_id != snapshot.branch_base_layer_id
        {
            return Ok(AddLayerResult::HeadMoved {
                expected: snapshot.branch_base_layer_id,
                actual: snapshot.layer_stack_head_id,
            });
        }
        if snapshot.commit_root_id == snapshot.base_root_id {
            return Ok(AddLayerResult::NoChanges {
                head_layer_id: snapshot.branch_base_layer_id,
            });
        }
        let layer = LayerRecord {
            id: LayerId::derive(
                snapshot.layer_stack_id,
                Some(snapshot.branch_base_layer_id),
                snapshot.commit_root_id,
            ),
            layer_stack_id: snapshot.layer_stack_id,
            parent_layer_id: Some(snapshot.branch_base_layer_id),
            root_id: snapshot.commit_root_id,
            source_branch_id: Some(branch_id),
            source_commit_id: Some(snapshot.head_commit_id),
        };
        let mut connection = self.db.writer()?;
```

## 23. End removes ephemeral state without implicitly committing

End serializes against other lifecycle work and quiesces activity. Clean end rejects unresolved/pending unpublished state and dirty projections. Discard is the explicit path for abandoning changes; it must remain usable even after backing-write failures, which is why its cleanup path differs from a clean freeze.

The method tears down the projection and finalizes workspace runtime cleanup. Failure to detach can mark BrokenCleanup rather than claiming success. The registry retains bounded session/execution information for observation, while the durable Branch/Commit/Layer records remain in the Store. Published history does not depend on keeping the mount alive.

Returning to our example: commit preserves `hello` in the Branch, Add shares that root as a Layer, and clean end releases the working environment. Ending a dirty workspace with discard deliberately skips the commit/publication path.

```bash
sed -n '912,925p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
        let presentation_failed = workspace.presentation_failed;
        drop(workspace);
        if state == WorkspaceState::BrokenCleanup
            || (presentation_failed && mode == EndWorkspaceMode::Clean)
        {
            return Err(WorkspaceError::InvalidPlacement);
        }
        // Discard must remain usable after a backing write failure. FREEZE
        // flushes pending bytes and rejects a failed owner; shutdown below
        // closes admission and releases those bytes without publishing them.
        if mode == EndWorkspaceMode::Clean {
            crate::projection::pause(&worker)?;
        }
        let _quiesced = match worker.quiesce() {
```

```bash
sed -n '939,953p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
            if mode == EndWorkspaceMode::Clean && active {
                let has_unpublished_state = {
                    let workspace = worker
                        .workspace
                        .lock()
                        .map_err(|_| WorkspaceError::WorkspaceBusy)?;
                    workspace.resolution.is_some() || workspace.pending_stage.is_some()
                };
                if has_unpublished_state || crate::projection::is_dirty(&worker)? {
                    return Err(WorkspaceError::WorkspaceDirty);
                }
            }
            let workspace = worker
                .workspace
                .lock()
```

```bash
sed -n '970,979p' crates/layerfs-workspace/src/lifecycle.rs
```

```output
        crate::projection::record_read_metrics(&worker)?;
        if let Err(error) = crate::projection::end(&worker) {
            if let Ok(mut workspace) = worker.workspace.lock() {
                workspace.state = WorkspaceState::BrokenCleanup;
                if let Ok(mut remote) = worker.remote.lock() {
                    *remote = None;
                }
            }
            return Err(error);
        }
```

## 24. Query and diff expose history; monitoring separates kinds of cost

Query is a typed request containing a kind, optional continuation, page limit, and optional LayerStack scope. Its default page size is 512. QueryPage carries records and a continuation; `into_next_query` preserves the prior scope/options when advancing. CLI query routing consumes these pages. Diff has a separate OperationHandle and spool-backed page delivery in `sdk/request.rs`, fed by Store traversal and the content layer's structural diff algorithms.

Monitor retains at most 512 operation receipts. It combines database snapshots, workspace summaries, semantic operation results, and optional dedup analysis. Candidate equations require candidate = inserted + reused, with additional accounting for admission batches and final insertion. The SDK associates these receipts with operations rather than treating every SQL statement as a user operation.

Canonical inserted bytes measure logical content growth. Pack compression, decoding work, SQLite allocation, spool use, and timings describe different costs. A small SQLite file-growth measurement alone is not proof that few semantic bytes were added, and a high reuse ratio does not imply that a workload performed little I/O.

```bash
sed -n '13,44p' crates/layerfs-sdk/src/query.rs
```

```output
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Query {
    kind: QueryKind,
    after: Option<Vec<u8>>,
    limit: u16,
    layer_stack_id: Option<LayerStackId>,
}

impl Query {
    pub fn new(kind: QueryKind) -> Self {
        Self {
            kind,
            after: None,
            limit: 512,
            layer_stack_id: None,
        }
    }

    pub fn after(mut self, continuation: Vec<u8>) -> Self {
        self.after = Some(continuation);
        self
    }

    pub fn limit(mut self, limit: u16) -> Self {
        self.limit = limit;
        self
    }

    pub fn in_layer_stack(mut self, layer_stack_id: LayerStackId) -> Self {
        self.layer_stack_id = Some(layer_stack_id);
        self
    }
```

```bash
sed -n '83,94p' crates/layerfs-sdk/src/query.rs
```

```output

impl QueryPage {
    pub fn into_next_query(self, prior: &Query) -> Option<Query> {
        self.continuation
            .map(|continuation| prior.clone().after(continuation))
    }
}
```

```bash
sed -n '74,89p' crates/layerfs-monitor/src/operation.rs
```

```output
impl CandidateStats {
    pub fn validate(self) -> bool {
        self.validate_for(OperationFamily::WorkspaceCommit)
    }

    pub fn validate_for(self, _family: OperationFamily) -> bool {
        self.candidate_objects == self.inserted_objects + self.reused_objects
            && self.candidate_bytes == self.inserted_bytes + self.reused_bytes
            && self.inserted_objects == self.batch_inserted_objects + self.final_inserted_objects
            && self.inserted_bytes == self.batch_inserted_bytes + self.final_inserted_bytes
            && self.reused_objects == self.preexisting_reused_objects
            && self.reused_bytes == self.preexisting_reused_bytes
            && self.max_transaction_objects <= ADMISSION_BATCH_COUNT as u64
            && self.max_transaction_bytes < OBJECT_PAGE_BYTES as u64
    }
}
```

```bash
sed -n '10,17p' crates/layerfs-monitor/src/collector.rs
```

```output
const RETAINED_OPERATIONS: usize = 512;

pub struct Monitor {
    store: Arc<LayerStackStore>,
    workspaces: Arc<Workspaces>,
    operations: Mutex<VecDeque<OperationReceipt>>,
    last_analysis: Mutex<Option<DedupAnalysis>>,
}
```

```bash
sed -n '29,45p' crates/layerfs-monitor/src/collector.rs
```

```output
    pub fn record(&self, receipt: OperationReceipt) -> MonitorResult<()> {
        if receipt
            .candidate
            .is_some_and(|candidate| !candidate.validate_for(receipt.operation.family))
        {
            return Err(MonitorError::Integrity("candidate equation"));
        }
        let mut operations = self
            .operations
            .lock()
            .map_err(|_| MonitorError::Integrity("operation receipts"))?;
        operations.push_back(receipt);
        while operations.len() > RETAINED_OPERATIONS {
            operations.pop_front();
        }
        Ok(())
    }
```

## 25. Read the tests as executable statements of the boundaries

The test layout mirrors the architecture. Content tests cover canonical encodings, namespace/inode behavior, rope models, and shifted-stream CDC. Store tests exercise history ownership, schema compatibility, admission, reconstruction, and publication failures. Workspace tests cover file edits, reconciliation, checkpoint/lifecycle behavior, and fault recovery. SDK tests cover public lifecycle and pagination, leases, and live FUSE/Docker scenarios; CLI tests cover command routing and managed sessions.

The repository's fast test script builds the full workspace with all features, then runs test executables with bounded parallelism while each executable uses one test thread. CI separately checks formatting and Clippy. Live tests have environmental requirements and gates; a native test run should not be assumed to exercise every mounted/container path.

The evaluator is intentionally small: `layerfs-eval check` connects to a Store, pins a Branch, reads its root object, and prints head/root. It does not recursively prove the integrity of every descendant. The benchmark families and release evidence are useful for performance and qualification work, but recorded historical results are not measurements of this walkthrough run.

```bash
rg --files crates | grep '/tests/' | sort
```

```output
crates/layerfs-cli/tests/v4.rs
crates/layerfs-content/tests/canonical_v2_fixture_oracle.rs
crates/layerfs-content/tests/extent_codec.rs
crates/layerfs-content/tests/extent_model.rs
crates/layerfs-content/tests/fastcdc_shifted_stream.rs
crates/layerfs-content/tests/logical.rs
crates/layerfs-content/tests/namespace_codec.rs
crates/layerfs-content/tests/namespace_model.rs
crates/layerfs-fuse/tests/proxy.rs
crates/layerfs-layerstack-store/tests/v4.rs
crates/layerfs-layerstack-store/tests/v5.rs
crates/layerfs-materialization/tests/materialize.rs
crates/layerfs-monitor/tests/monitor.rs
crates/layerfs-sdk/tests/leases.rs
crates/layerfs-sdk/tests/live_docker.rs
crates/layerfs-sdk/tests/live_fuse.rs
crates/layerfs-sdk/tests/query_pagination.rs
crates/layerfs-sdk/tests/v4.rs
crates/layerfs-workspace/tests/file_edit.rs
crates/layerfs-workspace/tests/reconciliation.rs
```

```bash
sed -n '1,20p' tools/test-fast.sh
```

```output
#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
jobs=${LAYERFS_TEST_JOBS:-4}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] && (( jobs <= 16 )) || {
  printf 'test-fast: LAYERFS_TEST_JOBS must be 1..16\n' >&2
  exit 2
}

temporary=$(mktemp -d "${TMPDIR:-/tmp}/layerfs-test-fast.XXXXXX")
trap 'rm -rf -- "$temporary"' EXIT

cargo test --manifest-path "$repo/Cargo.toml" --workspace --all-features \
  --locked \
  --no-run --message-format=json >"$temporary/artifacts.jsonl"
started=$SECONDS

python3 - "$temporary/artifacts.jsonl" "$jobs" <<'PY'
import concurrent.futures
```

```bash
sed -n '10,27p' tools/layerfs-eval/src/main.rs
```

```output
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    let (store, branch_id) = match arguments.as_slice() {
        [mode, store, id] if mode == "check" => {
            (LayerStackStore::connect(store)?, id.parse::<BranchId>()?)
        }
        _ => return Err("usage: layerfs-eval check <store-db> <branch-id>".into()),
    };
    let pinned = store.pin_branch(branch_id)?;
    pinned.reader.read_object(pinned.root)?;
    println!("{:?} {}", pinned.branch.head_commit_id, pinned.root);
    Ok(())
}
```

## 26. Trace one write from start to finish

With the pieces in place, follow `hello` through the system:

1. The CLI context owner or Rust caller invokes Client, which routes workspace creation to Workspaces and pins the selected Branch root.
2. The projection exposes that root. A materialized write changes a host file and is later captured; a FUSE write becomes a prepared live edit backed by retained ranges.
3. Commit pauses and quiesces the projection, obtains a coherent generation, and translates changed files and bindings into immutable candidate objects.
4. The content layer chunks new bytes, splices or builds extent trees, writes inode/metadata records, and produces the new namespace root while sharing unchanged objects.
5. Checked admission stores new physical records and reuses existing IDs. The Store retains the candidate stage, verifies expected history, and atomically inserts the Commit and advances the Branch.
6. Checkpoint installation updates the live baseline and releases obsolete backing when safe; projection recovery is handled separately if this step fails.
7. Add can make the Branch head root the next Layer, subject to the LayerStack head check. End tears down the workspace without changing already published history.

For further reading, use the same order through `client.rs`, `workspace/lifecycle.rs`, `workspace/changes.rs`, the content algorithms, and `layerstack-store/workspace.rs`. At each handoff ask what is immutable, who owns mutable state or backing, which generation/head is expected, and whether the root is only constructed, staged, or publicly reachable. Those questions explain both the normal data flow and the recovery paths.

To check this document against the checkout, run `uvx showboat --workdir ../layerfs verify walkthrough.md` from this analysis directory. Verification replays the source excerpts and compares their outputs; it does not run the application test suite.

## 27. Follow the read path back from a snapshot to bytes

The write trace ends with a root ID. Reading explains why that root is sufficient. In `filesystem::resolve`, decode the NamespaceRoot, obtain its inode table and root inode, then process one path component at a time. Each step looks up a name in the current directory tree and resolves the returned inode through the same snapshot's inode table. The final inode record identifies the file content and metadata. This is a traversal of one immutable snapshot; no previous Commit needs to be replayed.

This low-level resolver requires intermediate components to be directories. It does not implement general operating-system symlink following. `readlink` returns the stored target; mounted filesystem traversal has its own kernel/adapter behavior. Keeping this distinction prevents assuming that every logical API has POSIX path resolution semantics.

```bash
sed -n '30,56p' crates/layerfs-content/src/filesystem/resolve.rs
```

```output
pub fn resolve<S: ObjectRead>(
    store: &S,
    root: ObjectId,
    path: &CanonicalPath,
    counters: &mut LogicalCounters,
) -> CoreResult<Resolved> {
    let namespace = namespace(store, root)?;
    let table = InodeTableRoot(namespace.inode_table_root);
    let mut inode = namespace.root_directory_inode;
    let mut record = load_record(store, table, inode, counters)?;
    for component in path.components() {
        if record.kind != crate::tree::inode::InodeKind::Directory {
            return Err(CoreError::InvalidRecord(
                "path component is not a directory",
            ));
        }
        inode = directory_lookup(
            store,
            DirectoryStateRoot(record.content_root),
            &CanonicalName::from_bytes(component)?,
            &mut counters.namespace,
        )?
        .ok_or(CoreError::MissingObject)?;
        record = load_record(store, table, inode, counters)?;
    }
    Ok(Resolved { inode, record })
}
```

`filesystem::read_range` verifies that the resolved inode is a regular file and delegates to the rope with its content root. The rope loads FileState and the mapping root, checks the mapping summary, and descends into extents intersecting the requested range. Its batching constant caps selected payload objects at 127. The content API rejects reversed or out-of-bounds ranges; the live Workspace ReadPlan shown earlier clamps reads at EOF. These are different API contracts.

The Store underneath ObjectRead resolves physical pack locations and reconstructs canonical bytes. Authentication happens before decoding exposes an object to the caller. The generic default below recomputes ObjectId, while optimized readers may override the method and preserve the same guarantee. A corrupt demanded object fails the read rather than silently yielding bytes from another snapshot.

```bash
sed -n '77,99p' crates/layerfs-content/src/filesystem/read.rs
```

```output
}

pub fn read_range<S: ObjectRead, W: Write>(
    store: &S,
    root: ObjectId,
    path: &CanonicalPath,
    range: Range<u64>,
    sink: W,
) -> CoreResult<LogicalCounters> {
    let mut counters = LogicalCounters::default();
    let resolved = resolve(store, root, path, &mut counters)?;
    if resolved.record.kind != InodeKind::RegularFile {
        return Err(CoreError::WrongLogicalRole);
    }
    counters.rope = rope::read_range(
        store,
        FileStateRoot(resolved.record.content_root),
        range,
        sink,
    )?;
    Ok(counters)
}

```

```bash
sed -n '52,70p' crates/layerfs-content/src/file/rope/read.rs
```

```output

pub fn read_plan<S: ObjectRead>(
    store: &S,
    root: FileStateRoot,
    counters: &mut RopeCounters,
) -> CoreResult<ReadPlan> {
    let state = state(store, root, counters)?;
    let summary = Summary {
        id: state.mapping_root,
        bytes: state.logical_len,
        extents: state.extent_count,
        level: state.tree_level,
    };
    counters.nodes_read = add(counters.nodes_read, 1)?;
    let mapping = store.with_authenticated_canonical(summary.id, |canonical| {
        decode_node_with_context(canonical, true)
    })?;
    validate_summary(&mapping, summary)?;
    Ok(ReadPlan { state, mapping })
```

```bash
sed -n '3,15p' crates/layerfs-content/src/object/access.rs
```

```output
pub trait ObjectRead {
    fn get(&self, id: ObjectId) -> CoreResult<Vec<u8>>;

    fn with_authenticated_canonical<T, F>(&self, id: ObjectId, callback: F) -> CoreResult<T>
    where
        F: FnOnce(&[u8]) -> CoreResult<T>,
    {
        let bytes = self.get(id)?;
        if ObjectId::for_bytes(&bytes) != id {
            return Err(CoreError::IdentityMismatch);
        }
        callback(&bytes)
    }
```

## 28. Identify the exact moments when mutable state changes

There are three distinct synchronization concerns. Per-inode revision checks protect an individual prepared edit; the workspace mutation generation identifies a coherent collection of changes; expected Branch head/base checks protect publication to shared history. None substitutes for the others.

`apply_edit` checks that both the revision and previous file data still match the prepared plan. It computes generation, revision, and resource charges before replacing file data. Only then does it update accounting and mark the node and all of its known paths dirty. A rejected stale plan therefore cannot install its bytes as the new logical state. The adapter still owns any backing reservation or bytes written while preparing that attempt.

```bash
sed -n '287,324p' crates/layerfs-workspace-core/src/file_edit.rs
```

```output
    pub fn apply_edit(&mut self, prepared: PreparedFileEdit) -> Result<usize> {
        if !self.nodes.get(&prepared.node).is_some_and(|node| {
            node.revision == prepared.expected_revision
                && matches!(&node.data, Data::File(data) if *data == prepared.before)
        }) {
            return Err(Error::Integrity("stale prepared write"));
        }
        let generation = self
            .mutation_generation
            .checked_add(prepared.generations)
            .ok_or(Error::Integrity("Workspace mutation generation"))?;
        let revision = prepared
            .expected_revision
            .checked_add(1)
            .ok_or(Error::Integrity("inode revision"))?;
        let (inline, allocation, spool) = self.write_resources(&prepared)?;
        let node = self
            .nodes
            .get_mut(&prepared.node)
            .expect("validated prepared inode");
        node.data = Data::File(prepared.next);
        node.revision = revision;
        self.inline_bytes = inline;
        self.piece_allocation_bytes = allocation;
        self.spool_bytes = spool;
        self.spool_bytes_peak = self.spool_bytes_peak.max(spool);
        self.edited_nodes.insert(prepared.node);
        self.dirty.insert(prepared.node);
        self.mutation_generation = generation;
        for path in &node.paths {
            self.mutation_paths.insert(path.clone(), generation);
        }
        Ok(prepared.bytes)
    }

    fn write_resources(&self, prepared: &PreparedFileEdit) -> Result<(u64, u64, u64)> {
        let old = match &prepared.before {
            FileData::Edited { pieces, .. } => Some(pieces),
```

For FUSE, `LiveOwner::freeze` establishes the larger snapshot boundary. It flushes kernel cache through the operation gate, retains an operation cut, flushes pending append work, publishes frozen facts, and retires ranges. The host candidate builder consumes those facts. Merely locking the host Workspace would not establish this boundary when execution-side state and kernel caches also participate.

```bash
sed -n '2404,2418p' crates/layerfs-fuse/src/live_owner.rs
```

```output
    pub async fn freeze(&self) -> PortResult<()> {
        if self.0.failed.load(Ordering::Acquire) {
            return Err(PortError::Io);
        }
        if self.0.cut.lock().map_err(|_| PortError::Io)?.is_none() {
            let flush = self.0.gate.cache_flush().await;
            self.flush_kernel_cache().await?;
            let cut = flush.finish().await;
            *self.0.cut.lock().map_err(|_| PortError::Io)? = Some(cut);
        }
        self.flush_append(&mut *self.0.append.lock().await).await?;
        self.publish_facts().await?;
        self.retire_ranges().await
    }

```

After publication, checkpoint installation makes committed file roots the new Base state and clears installed directory overlays. The caller must validate the complete bounded journal before installing its first record, and retain the journal for retry. `finish_checkpoint` recalculates charges for edited nodes that remain retained, switches the base root, invalidates acquired-name knowledge, and resets mutation tracking. In particular, an unlinked but pinned file can still require retained backing after named files have moved to their committed roots.

This closes the loop: immutable snapshot → mutable overlays → frozen candidate → checked publication → new immutable baseline. Failure before publication and failure during baseline installation require different recovery actions, as sections 20–21 describe.

```bash
sed -n '28,63p' crates/layerfs-workspace-core/src/checkpoint.rs
```

```output

    /// The caller validates the complete bounded checkpoint journal before the
    /// first install, and retains it for exact retry after any partial failure.
    pub fn install_checkpoint_record(
        &mut self,
        id: NodeId,
        inode: InodeId,
        content: ObjectId,
        attr: Attr,
    ) -> Result<()> {
        self.validate_checkpoint_record(id, inode, attr)?;
        let node = self
            .nodes
            .get_mut(&id)
            .ok_or(Error::Integrity("checkpoint node"))?;
        match &mut node.data {
            Data::File(data) => {
                *data = FileData::Base {
                    root: layerfs_content::file::rope::FileStateRoot(content),
                    len: attr.size,
                }
            }
            Data::Directory(directory) => {
                directory.base = Some(layerfs_content::tree::directory::DirectoryStateRoot(
                    content,
                ));
                directory.changes.clear();
            }
            Data::Symlink(_) => {}
        }
        self.edited_nodes.remove(&id);
        node.canonical = Some(inode);
        self.canonical_nodes.insert(inode, id);
        Ok(())
    }

```

```bash
sed -n '83,105p' crates/layerfs-workspace-core/src/checkpoint.rs
```

```output
                ..
            }) = &self.nodes[id].data
            else {
                return Err(Error::Integrity("checkpoint retained spool"));
            };
            self.spool_bytes = self.spool_bytes.saturating_add(*spool_high_water);
            self.inline_bytes = self.inline_bytes.saturating_add(pieces.inline_len());
            self.piece_allocation_bytes = self
                .piece_allocation_bytes
                .saturating_add(pieces.logical_allocation_charge()?);
        }
        self.base_root = root;
        self.known_names.clear();
        self.spool_bytes_peak = self.spool_bytes;
        self.mutation_generation = 0;
        self.mutation_paths.clear();
        self.dirty.clear();
        Ok(())
    }
}
```

## 29. Source map for a second, deeper pass

Use this map after reading the linear route. Paths are relative to the repository root; each row identifies the implementation boundary where a change or investigation should begin.

| Question | Source entry | Continue into |
| --- | --- | --- |
| How does a command survive separate CLI invocations? | [CLI runtime](../layerfs/crates/layerfs-cli/src/runtime.rs) | `CliSession` and command dispatch in `layerfs-cli/src/lib.rs` |
| Which services does the public API own? | [SDK client](../layerfs/crates/layerfs-sdk/src/client.rs) | `query.rs`, `request.rs`, `result.rs` |
| What defines an object's identity? | [Object digest](../layerfs/crates/layerfs-content/src/object/digest.rs) | `codec.rs`, `canonical.rs`, `access.rs`, `references.rs` |
| How are new bytes chunked and existing extents reused? | [Rope builder](../layerfs/crates/layerfs-content/src/file/rope/build.rs) | `file/cdc`, `rope/edit.rs`, `rope/read.rs` |
| How are names, hard links, and metadata represented? | [Namespace root](../layerfs/crates/layerfs-content/src/tree/root.rs) | `tree/directory`, `tree/inode`, `tree/metadata`, `tree/batch.rs` |
| How do paths and logical edits use those structures? | [Filesystem resolver](../layerfs/crates/layerfs-content/src/filesystem/resolve.rs) | `read.rs`, `apply.rs`, `diff.rs`, `reconcile.rs` |
| Where are canonical objects physically stored? | [Object storage](../layerfs/crates/layerfs-layerstack-store/src/objects.rs) | `objects/admission.rs`, `pack.rs`, `read.rs`, `spill.rs`, `sql/schema/v7.sql` |
| Who owns the Branch publication transaction? | [Store workspace operations](../layerfs/crates/layerfs-layerstack-store/src/workspace.rs) | `staging.rs`, `sql/workspace/advance_branch.sql` |
| Where are branching and promotion implemented? | [Branch operations](../layerfs/crates/layerfs-layerstack-store/src/branch.rs) | `layerstack.rs`, `records.rs`, `ids.rs` |
| Who manages session and execution lifetimes? | [Workspace registry](../layerfs/crates/layerfs-workspace/src/registry.rs) | `lifecycle.rs`, `worker.rs`, `session.rs`, `execution.rs`, `output.rs` |
| What can mutate without doing physical I/O? | [Portable live core](../layerfs/crates/layerfs-workspace-core/src/lib.rs) | `namespace.rs`, `file_edit.rs`, `backing.rs`, `checkpoint.rs`, `limits.rs` |
| How does the host build a candidate? | [Workspace changes](../layerfs/crates/layerfs-workspace/src/changes.rs) | `cow_tree.rs`, `file_io.rs`, `live_backing.rs`, `capture.rs`, `reconcile.rs` |
| Which projection is attached? | [Projection selection](../layerfs/crates/layerfs-workspace/src/projection.rs) | `layerfs-materialization/src/{materialize,capture}.rs`, `docker.rs` |
| Who serves mounted filesystem requests? | [FUSE callbacks](../layerfs/crates/layerfs-fuse/src/filesystem.rs) | `port.rs`, `live_owner.rs`, `live_runtime.rs`, `live_transport.rs`, `live_wire.rs`, `handles.rs`, `inode_table.rs` |
| Who controls container processes and mounts? | [Daemon protocol](../layerfs/crates/layerfs-daemon/src/protocol.rs) | daemon `lib.rs`, workspace `daemon.rs`, `container.rs`, `docker_engine.rs` |
| Which measurements belong to an operation? | [Monitor collector](../layerfs/crates/layerfs-monitor/src/collector.rs) | `operation.rs`, `snapshot.rs`, `dedup.rs`, Store `telemetry.rs` |

The main route covers all ten implementation crates. It intentionally selects representative functions rather than reproducing every helper, codec branch, test fixture, or historical benchmark artifact. Source-excerpt replay checks quoted code; architectural explanations are a reading of the implementation, not an application execution test.

## 30. Architecture overview

Open the [LayerFS architecture diagram](architecture.html) in a browser. It is a self-contained HTML file with inline SVG, using the approved default palette. Read the entry point and coordinator first, then the alternative projections, shared mutable model, host content algorithms and Store.

The diagram emphasizes two owners: Workspaces coordinates the mutable session, while the Store controls history publication. The FUSE live owner may run locally or on the execution side in a container; canonical construction and SQLite remain on the host. Materialization writes an ordinary directory and captures its changes before commit. The portable core supplies live-state transformations to the adapters.

The nine nodes group CLI with SDK, daemon with execution, and Store with SQLite. Arrows show selected calls and uses, not every crate dependency or every data transfer. Direct SDK history operations, Monitor inputs, codec internals and checkpoint return paths are explained in the preceding sections. Commit publication and projection recovery remain distinct outcomes.

The following source anchors explain the ownership and projection boundaries shown in the diagram.

```bash
sed -n '24,32p' crates/layerfs-sdk/src/client.rs
sed -n '16,30p' crates/layerfs-workspace/src/projection.rs
sed -n '1,10p' crates/layerfs-fuse/src/live_owner.rs
```

```output
#[derive(Clone)]
pub struct Client(Arc<ClientInner>);

struct ClientInner {
    store: Arc<LayerStackStore>,
    workspaces: Arc<Workspaces>,
    monitor: Arc<Monitor>,
}

    Materialized(PathBuf),
    Docker(Box<crate::docker::DockerProjection>),
    #[cfg(all(target_os = "linux", feature = "host-fuse"))]
    Fuse(layerfs_fuse::HostMount),
}

pub(crate) fn attach(
    worker: &Arc<WorkspaceWorker>,
    daemon: Option<&crate::daemon::DaemonOwner>,
) -> WorkspaceResult<ProjectionHandle> {
    if let WorkspacePlacement::Container { container_id, root } = &worker.request.placement {
        if worker.projection != crate::WorkspaceProjection::Fuse {
            return Err(WorkspaceError::InvalidPlacement);
        }
        let (remote, runtime) = {
//! Execution-side live state. Physical storage and canonical construction stay on the host.
use crate::live_runtime::{LiveRuntime, OperationGate, Scheduler};
use crate::live_transport::BackingConnection;
use crate::live_wire::{self as wire, Input};
use crate::port::{DirectoryPage, KernelEntry, KernelReferences};
use crate::{Attr, FilesystemPort, Kind, NodeId, PortError, PortResult, ROOT};
use layerfs_workspace_core::backing::{BackingId, BackingRef};
use layerfs_workspace_core::file_edit::{Piece, SpoolSlice};
use layerfs_workspace_core::namespace::{AcquiredInode, NameLookup, ResolvedName};
use layerfs_workspace_core::{Data, LiveWorkspace, ReadPlan, ReadSource, ResourcePolicy};
```
