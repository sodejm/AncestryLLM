"""Compatibility façade for supported incremental GEDCOM synchronization.

Implementation ownership is split across contracts, deterministic algorithms,
manifest validation, publication/recovery, orchestration, and terminal translation.
"""

from __future__ import annotations

from ancestryllm.gedcom import sync_algorithms as _algorithms
from ancestryllm.gedcom import sync_cli as _cli
from ancestryllm.gedcom import sync_contracts as _contracts
from ancestryllm.gedcom import sync_manifest as _manifest
from ancestryllm.gedcom import sync_operations as _operations
from ancestryllm.gedcom import sync_publication as _publication

_block_has_citation = _algorithms._block_has_citation
_block_key = _algorithms._block_key
_block_logical_identity = _algorithms._block_logical_identity
_block_parts = _algorithms._block_parts
_citation_identity = _algorithms._citation_identity
_direct_blocks = _algorithms._direct_blocks
_family_semantic_key = _algorithms._family_semantic_key
_hash_text = _algorithms._hash_text
_identifier_values = _algorithms._identifier_values
_identity_fingerprint = _algorithms._identity_fingerprint
_is_removable_fact = _algorithms._is_removable_fact
_json_bytes = _algorithms._json_bytes
_map_nonpeople = _algorithms._map_nonpeople
_match_people = _algorithms._match_people
_merge_citations = _algorithms._merge_citations
_merge_compatible_structure = _algorithms._merge_compatible_structure
_merge_family_records = _algorithms._merge_family_records
_merge_same_fact = _algorithms._merge_same_fact
_next_pointer = _algorithms._next_pointer
_normal_place = _algorithms._normal_place
_normal_space = _algorithms._normal_space
_normal_value = _algorithms._normal_value
_person_allocation_key = _algorithms._person_allocation_key
_person_from_record = _algorithms._person_from_record
_quality_report = _algorithms._quality_report
_reconcile_person_blocks = _algorithms._reconcile_person_blocks
_record_allocation_key = _algorithms._record_allocation_key
_record_semantic_key = _algorithms._record_semantic_key
_relative_lines = _algorithms._relative_lines
_render_list = _algorithms._render_list
_render_update_report = _algorithms._render_update_report
_replace_header_pointer = _algorithms._replace_header_pointer
_rewrite_lines = _algorithms._rewrite_lines
_seed_snapshot_history = _algorithms._seed_snapshot_history
_sha256_file = _algorithms._sha256_file
_singleton_values = _algorithms._singleton_values
_structure_key = _algorithms._structure_key
_verdict_confidence = _algorithms._verdict_confidence

PlainEnglishArgumentParser = _cli.PlainEnglishArgumentParser
_build_rebase_parser = _cli._build_rebase_parser
_build_update_parser = _cli._build_update_parser
_rebase_command_from_namespace = _cli._rebase_command_from_namespace
_update_command_from_namespace = _cli._update_command_from_namespace
execute = _cli.execute

ATTACHMENT_TAGS = _contracts.ATTACHMENT_TAGS
CONTROLLED_TAGS = _contracts.CONTROLLED_TAGS
EXIT_CODES = _contracts.EXIT_CODES
MANIFEST_SCHEMA_VERSION = _contracts.MANIFEST_SCHEMA_VERSION
RECORD_PREFIXES = _contracts.RECORD_PREFIXES
SOURCE_ADMIN_TAGS = _contracts.SOURCE_ADMIN_TAGS
SOURCE_ID_RE = _contracts.SOURCE_ID_RE
SUPPORTED_VENDORS = _contracts.SUPPORTED_VENDORS
CancellationCheck = _contracts.CancellationCheck
ResolverFactory = _contracts.ResolverFactory
SnapshotSpec = _contracts.SnapshotSpec
SyncAccounting = _contracts.SyncAccounting
SyncCommand = _contracts.SyncCommand
SyncError = _contracts.SyncError
SyncExecutionResult = _contracts.SyncExecutionResult
SyncRebaseCommand = _contracts.SyncRebaseCommand
SyncSnapshotInput = _contracts.SyncSnapshotInput
SyncStats = _contracts.SyncStats
SyncUpdateCommand = _contracts.SyncUpdateCommand
_checkpoint = _contracts._checkpoint

_header_export_date = _manifest._header_export_date
_load_manifest = _manifest._load_manifest
_manifest_invalid = _manifest._manifest_invalid
_manifest_timestamp = _manifest._manifest_timestamp
_new_manifest = _manifest._new_manifest
_parse_snapshot_argument = _manifest._parse_snapshot_argument
_snapshot_inputs_from_arguments = _manifest._snapshot_inputs_from_arguments
_snapshot_specs = _manifest._snapshot_specs
_validate_exported_at = _manifest._validate_exported_at
_validate_manifest = _manifest._validate_manifest
_validate_snapshot_continuity = _manifest._validate_snapshot_continuity
_validate_snapshot_identity = _manifest._validate_snapshot_identity
_verify_manifest_artifacts = _manifest._verify_manifest_artifacts

_execute_typed_command = _operations._execute_typed_command
_master_block_index = _operations._master_block_index
_normalize_command_paths = _operations._normalize_command_paths
_perform_rebase = _operations._perform_rebase
_perform_update = _operations._perform_update
_rebase_accounting = _operations._rebase_accounting
_sync_accounting = _operations._sync_accounting
_with_error_contract = _operations._with_error_contract
execute_command = _operations.execute_command

_STAGING_CLEANUP_NAMES = _publication._STAGING_CLEANUP_NAMES
_capability_current_path = _publication._capability_current_path
_capability_matches_selected = _publication._capability_matches_selected
_cleanup_capability_tree = _publication._cleanup_capability_tree
_cleanup_empty_release_root = _publication._cleanup_empty_release_root
_cleanup_owned_flat_directory = _publication._cleanup_owned_flat_directory
_cleanup_preselected_empty_directory = _publication._cleanup_preselected_empty_directory
_cleanup_staging_directory = _publication._cleanup_staging_directory
_close_capability = _publication._close_capability
_close_capability_quietly = _publication._close_capability_quietly
_close_descriptor_quietly = _publication._close_descriptor_quietly
_create_staging_directory = _publication._create_staging_directory
_delete_held_marker = _publication._delete_held_marker
_directory_identity = _publication._directory_identity
_DirectoryCapability = _publication._DirectoryCapability
_DirectoryIdentity = _publication._DirectoryIdentity
_ensure_release_root = _publication._ensure_release_root
_exclusive_rename_directory = _publication._exclusive_rename_directory
_finalize_published_directory = _publication._finalize_published_directory
_held_file_path = _publication._held_file_path
_held_staging_location = _publication._held_staging_location
_is_flat_cleanup_entry = _publication._is_flat_cleanup_entry
_marker_identity_at = _publication._marker_identity_at
_open_directory_capability = _publication._open_directory_capability
_open_held_marker = _publication._open_held_marker
_open_plain_directory_descriptor = _publication._open_plain_directory_descriptor
_open_plain_directory_entry_descriptor = _publication._open_plain_directory_entry_descriptor
_open_windows_delete_descriptor = _publication._open_windows_delete_descriptor
_open_windows_shared_marker = _publication._open_windows_shared_marker
_prove_committed_unlinked_marker = _publication._prove_committed_unlinked_marker
_publication_destination_is_selected = _publication._publication_destination_is_selected
_publication_incomplete_error = _publication._publication_incomplete_error
_publication_root_changed_error = _publication._publication_root_changed_error
_PublicationTransactionState = _publication._PublicationTransactionState
_publish_and_finalize_directory = _publication._publish_and_finalize_directory
_publish_directory_no_clobber = _publication._publish_directory_no_clobber
_raw_directory_identity = _publication._raw_directory_identity
_recover_interrupted_publication = _publication._recover_interrupted_publication
_remove_capability_marker = _publication._remove_capability_marker
_remove_published_staging_marker = _publication._remove_published_staging_marker
_require_selected_capability = _publication._require_selected_capability
_rollback_published_directory = _publication._rollback_published_directory
_uses_windows_capability_handles = _publication._uses_windows_capability_handles
_windows_close_handle = _publication._windows_close_handle
_windows_create_file_handle = _publication._windows_create_file_handle
_windows_descriptor_from_handle = _publication._windows_descriptor_from_handle
_windows_mark_descriptor_for_deletion = _publication._windows_mark_descriptor_for_deletion
_windows_mark_handle_for_deletion = _publication._windows_mark_handle_for_deletion
_write_bytes = _publication._write_bytes

__all__ = [
    "ATTACHMENT_TAGS",
    "CONTROLLED_TAGS",
    "EXIT_CODES",
    "MANIFEST_SCHEMA_VERSION",
    "RECORD_PREFIXES",
    "SOURCE_ADMIN_TAGS",
    "SOURCE_ID_RE",
    "SUPPORTED_VENDORS",
    "CancellationCheck",
    "PlainEnglishArgumentParser",
    "ResolverFactory",
    "SnapshotSpec",
    "SyncAccounting",
    "SyncCommand",
    "SyncError",
    "SyncExecutionResult",
    "SyncRebaseCommand",
    "SyncSnapshotInput",
    "SyncStats",
    "SyncUpdateCommand",
    "execute",
    "execute_command",
]
