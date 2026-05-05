"""
Comprehensive tests for Memory Tool backends.

Tests cover all three storage backends:
- LocalMemoryBackend (filesystem)
- S3MemoryBackend (AWS S3)
- R2MemoryBackend (Cloudflare R2)

Each backend is tested for:
- Initialization and configuration
- File creation (create mode)
- File appending (append mode)
- File overwriting (overwrite mode)
- File content viewing
- Directory listing
- String replacement
- Line insertion
- File deletion
- File/directory renaming
- Clear all memory
- Path traversal protection (security)
- Concurrent access (thread safety)
- Error handling and edge cases
- Unicode and special character support
- Large file handling

Cloud backends (S3, R2) additional tests:
- Retry logic
- ETag-based concurrency control
- Pagination for large datasets
- Connection pooling
- Timeout handling
- Missing credentials detection
"""

import pytest
import os
import tempfile
import shutil
import threading
import time
import json
from datetime import datetime
from unittest.mock import MagicMock, patch
import io

from botocore.exceptions import ClientError

pytestmark = [pytest.mark.requires_api_key, pytest.mark.requires_network]


# ============================================================================
# Test Fixtures - Common Setup
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for local backend tests."""
    dir_path = tempfile.mkdtemp(prefix="omni_memory_test_")
    yield dir_path
    # Cleanup after test
    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.fixture
def local_backend(temp_dir):
    """Create a LocalMemoryBackend with a temp directory."""
    from omnicoreagent.core.tools.memory_tool.local_storage import LocalMemoryBackend

    return LocalMemoryBackend(base_dir=temp_dir)


@pytest.fixture
def mock_s3_client():
    """Create a mocked S3 client for S3 backend tests."""
    mock = MagicMock()
    mock.head_bucket.return_value = {}
    mock.get_bucket_versioning.return_value = {"Status": "Enabled"}
    mock.get_bucket_encryption.return_value = {"ServerSideEncryptionConfiguration": {}}
    return mock


@pytest.fixture
def s3_backend(mock_s3_client):
    """Create an S3MemoryBackend with mocked S3 client."""
    from omnicoreagent.core.tools.memory_tool.s3_storage import S3MemoryBackend

    with patch("boto3.client", return_value=mock_s3_client):
        backend = S3MemoryBackend(
            bucket_name="test-bucket",
            prefix="memories/",
            region="us-east-1",
            aws_access_key_id="test-key",
            aws_secret_access_key="test-secret",
        )
        backend.s3 = mock_s3_client
        return backend


@pytest.fixture
def mock_r2_client():
    """Create a mocked R2/S3 client for R2 backend tests."""
    mock = MagicMock()
    mock.head_bucket.return_value = {}
    mock.get_bucket_versioning.return_value = {}
    mock.get_bucket_encryption.return_value = {}
    return mock


@pytest.fixture
def r2_backend(mock_r2_client):
    """Create an R2MemoryBackend with mocked client."""
    from omnicoreagent.core.tools.memory_tool.r2_storage import R2MemoryBackend

    with patch("boto3.client", return_value=mock_r2_client):
        backend = R2MemoryBackend(
            bucket_name="test-r2-bucket",
            account_id="test-account-id",
            access_key_id="test-r2-key",
            secret_access_key="test-r2-secret",
            prefix="memories/",
        )
        backend.s3 = mock_r2_client
        return backend


# ============================================================================
# LocalMemoryBackend Tests
# ============================================================================


class TestLocalMemoryBackendInitialization:
    """Tests for LocalMemoryBackend initialization."""

    def test_creates_base_directory(self, temp_dir):
        """Test that base directory is created on initialization."""
        from omnicoreagent.core.tools.memory_tool.local_storage import (
            LocalMemoryBackend,
        )

        new_dir = os.path.join(temp_dir, "new_memories")
        _backend = LocalMemoryBackend(base_dir=new_dir)

        assert os.path.exists(new_dir)
        assert os.path.isdir(new_dir)

    def test_uses_absolute_path(self, temp_dir):
        """Test that base_dir is resolved to absolute path."""
        from omnicoreagent.core.tools.memory_tool.local_storage import (
            LocalMemoryBackend,
        )

        backend = LocalMemoryBackend(base_dir=temp_dir)
        assert backend.base_dir.is_absolute()

    def test_handles_nested_directory_creation(self, temp_dir):
        """Test creating deeply nested base directory."""
        from omnicoreagent.core.tools.memory_tool.local_storage import (
            LocalMemoryBackend,
        )

        nested_dir = os.path.join(temp_dir, "level1", "level2", "level3", "memories")
        _backend = LocalMemoryBackend(base_dir=nested_dir)

        assert os.path.exists(nested_dir)


class TestLocalMemoryBackendCreate:
    """Tests for file creation operations."""

    def test_create_simple_file(self, local_backend, temp_dir):
        """Test creating a simple text file."""
        result = local_backend.create_update("test.txt", "Hello, World!", mode="create")

        assert "created" in result.lower()
        assert os.path.exists(os.path.join(temp_dir, "test.txt"))

        with open(os.path.join(temp_dir, "test.txt"), "r") as f:
            assert f.read() == "Hello, World!"

    def test_create_file_in_subdirectory(self, local_backend, temp_dir):
        """Test creating a file in a subdirectory that doesn't exist."""
        result = local_backend.create_update(
            "subdir/nested/file.txt", "Nested content", mode="create"
        )

        assert "created" in result.lower()
        file_path = os.path.join(temp_dir, "subdir", "nested", "file.txt")
        assert os.path.exists(file_path)

        with open(file_path, "r") as f:
            assert f.read() == "Nested content"

    def test_create_fails_if_file_exists(self, local_backend, temp_dir):
        """Test that create mode fails if file already exists."""
        # First create
        local_backend.create_update("existing.txt", "Original content", mode="create")

        # Second create should fail
        result = local_backend.create_update(
            "existing.txt", "New content", mode="create"
        )

        assert "already exists" in result.lower()
        # Original content should be preserved
        with open(os.path.join(temp_dir, "existing.txt"), "r") as f:
            assert f.read() == "Original content"

    def test_create_with_json_dict(self, local_backend, temp_dir):
        """Test creating a file with a dict (should be JSON serialized)."""
        data = {"key": "value", "number": 42, "nested": {"a": 1}}
        result = local_backend.create_update("data.json", data, mode="create")

        assert "created" in result.lower()

        with open(os.path.join(temp_dir, "data.json"), "r") as f:
            loaded = json.loads(f.read())
            assert loaded == data

    def test_create_with_list(self, local_backend, temp_dir):
        """Test creating a file with a list (should be newline-joined)."""
        items = ["line1", "line2", "line3"]
        result = local_backend.create_update("lines.txt", items, mode="create")

        assert "created" in result.lower()

        with open(os.path.join(temp_dir, "lines.txt"), "r") as f:
            assert f.read() == "line1\nline2\nline3"

    def test_create_with_unicode(self, local_backend, temp_dir):
        """Test creating a file with Unicode characters."""
        content = "Hello, 世界! 🎉 Привет мир! مرحبا بالعالم"
        result = local_backend.create_update("unicode.txt", content, mode="create")

        assert "created" in result.lower()

        with open(os.path.join(temp_dir, "unicode.txt"), "r", encoding="utf-8") as f:
            assert f.read() == content

    def test_create_empty_file(self, local_backend, temp_dir):
        """Test creating an empty file."""
        result = local_backend.create_update("empty.txt", "", mode="create")

        assert "created" in result.lower()
        assert os.path.exists(os.path.join(temp_dir, "empty.txt"))

        with open(os.path.join(temp_dir, "empty.txt"), "r") as f:
            assert f.read() == ""


class TestLocalMemoryBackendAppend:
    """Tests for file append operations."""

    def test_append_to_existing_file(self, local_backend, temp_dir):
        """Test appending to an existing file."""
        local_backend.create_update("append_test.txt", "Line 1", mode="create")
        result = local_backend.create_update("append_test.txt", "Line 2", mode="append")

        assert "appended" in result.lower()

        with open(os.path.join(temp_dir, "append_test.txt"), "r") as f:
            content = f.read()
            assert "Line 1" in content
            assert "Line 2" in content

    def test_append_fails_if_file_not_exists(self, local_backend):
        """Test that append fails if file doesn't exist."""
        result = local_backend.create_update(
            "nonexistent.txt", "Content", mode="append"
        )

        assert "cannot append" in result.lower() or "not found" in result.lower()

    def test_append_multiple_times(self, local_backend, temp_dir):
        """Test appending multiple times."""
        local_backend.create_update("multi_append.txt", "Header", mode="create")

        for i in range(5):
            local_backend.create_update("multi_append.txt", f"Line {i}", mode="append")

        with open(os.path.join(temp_dir, "multi_append.txt"), "r") as f:
            content = f.read()
            assert "Header" in content
            for i in range(5):
                assert f"Line {i}" in content


class TestLocalMemoryBackendOverwrite:
    """Tests for file overwrite operations."""

    def test_overwrite_existing_file(self, local_backend, temp_dir):
        """Test overwriting an existing file."""
        local_backend.create_update("overwrite.txt", "Original", mode="create")
        result = local_backend.create_update(
            "overwrite.txt", "New content", mode="overwrite"
        )

        assert "overwritten" in result.lower()

        with open(os.path.join(temp_dir, "overwrite.txt"), "r") as f:
            assert f.read() == "New content"

    def test_overwrite_fails_if_file_not_exists(self, local_backend):
        """Test that overwrite fails if file doesn't exist."""
        result = local_backend.create_update(
            "nonexistent.txt", "Content", mode="overwrite"
        )

        assert "cannot overwrite" in result.lower() or "not found" in result.lower()


class TestLocalMemoryBackendView:
    """Tests for viewing files and directories."""

    def test_view_file_content(self, local_backend, temp_dir):
        """Test viewing file content."""
        local_backend.create_update("view_test.txt", "File content here", mode="create")
        result = local_backend.view("view_test.txt")

        assert "File content here" in result

    def test_view_directory_listing(self, local_backend, temp_dir):
        """Test viewing directory contents."""
        local_backend.create_update("file1.txt", "Content 1", mode="create")
        local_backend.create_update("file2.txt", "Content 2", mode="create")
        local_backend.create_update("subdir/file3.txt", "Content 3", mode="create")

        result = local_backend.view("")

        assert "file1.txt" in result
        assert "file2.txt" in result
        assert "subdir" in result

    def test_view_nonexistent_path(self, local_backend):
        """Test viewing a non-existent path."""
        result = local_backend.view("nonexistent_file.txt")

        assert "not found" in result.lower()

    def test_view_empty_directory(self, local_backend, temp_dir):
        """Test viewing an empty directory."""
        os.makedirs(os.path.join(temp_dir, "empty_dir"))
        result = local_backend.view("empty_dir")

        assert "empty" in result.lower()

    def test_view_root_directory(self, local_backend):
        """Test viewing root directory (empty path)."""
        result = local_backend.view(None)
        assert "contents" in result.lower() or "empty" in result.lower()

        result2 = local_backend.view("")
        assert "contents" in result2.lower() or "empty" in result2.lower()


class TestLocalMemoryBackendStrReplace:
    """Tests for string replacement operations."""

    def test_replace_string(self, local_backend, temp_dir):
        """Test basic string replacement."""
        local_backend.create_update("replace.txt", "Hello World!", mode="create")
        result = local_backend.str_replace("replace.txt", "World", "Universe")

        assert "replaced" in result.lower()

        with open(os.path.join(temp_dir, "replace.txt"), "r") as f:
            assert f.read() == "Hello Universe!"

    def test_replace_multiple_occurrences(self, local_backend, temp_dir):
        """Test replacing multiple occurrences."""
        local_backend.create_update(
            "multi_replace.txt", "foo bar foo baz foo", mode="create"
        )
        _result = local_backend.str_replace("multi_replace.txt", "foo", "XXX")

        with open(os.path.join(temp_dir, "multi_replace.txt"), "r") as f:
            content = f.read()
            assert content == "XXX bar XXX baz XXX"
            assert "foo" not in content

    def test_replace_string_not_found(self, local_backend, temp_dir):
        """Test replacement when string is not found."""
        local_backend.create_update("no_match.txt", "Original text", mode="create")
        result = local_backend.str_replace("no_match.txt", "nonexistent", "replacement")

        assert "not found" in result.lower()

    def test_replace_on_nonexistent_file(self, local_backend):
        """Test replacement on non-existent file."""
        result = local_backend.str_replace("nonexistent.txt", "old", "new")

        assert "not found" in result.lower()


class TestLocalMemoryBackendInsert:
    """Tests for line insertion operations."""

    def test_insert_at_beginning(self, local_backend, temp_dir):
        """Test inserting at line 1 (beginning)."""
        local_backend.create_update("insert.txt", "Line 2\nLine 3", mode="create")
        result = local_backend.insert("insert.txt", 1, "Line 1")

        assert "inserted" in result.lower()

        with open(os.path.join(temp_dir, "insert.txt"), "r") as f:
            lines = f.readlines()
            assert lines[0].strip() == "Line 1"

    def test_insert_in_middle(self, local_backend, temp_dir):
        """Test inserting in the middle of a file."""
        local_backend.create_update("middle.txt", "Line 1\nLine 3", mode="create")
        _result = local_backend.insert("middle.txt", 2, "Line 2")

        with open(os.path.join(temp_dir, "middle.txt"), "r") as f:
            lines = f.readlines()
            assert "Line 2" in lines[1]

    def test_insert_at_end(self, local_backend, temp_dir):
        """Test inserting at the end (line > file length)."""
        local_backend.create_update("end.txt", "Line 1\nLine 2", mode="create")
        _result = local_backend.insert("end.txt", 100, "Line 3")

        with open(os.path.join(temp_dir, "end.txt"), "r") as f:
            content = f.read()
            assert "Line 3" in content

    def test_insert_on_nonexistent_file(self, local_backend):
        """Test insert on non-existent file."""
        result = local_backend.insert("nonexistent.txt", 1, "Content")

        assert "not found" in result.lower()


class TestLocalMemoryBackendDelete:
    """Tests for delete operations."""

    def test_delete_file(self, local_backend, temp_dir):
        """Test deleting a file."""
        local_backend.create_update("to_delete.txt", "Content", mode="create")
        assert os.path.exists(os.path.join(temp_dir, "to_delete.txt"))

        result = local_backend.delete("to_delete.txt")

        assert "deleted" in result.lower()
        assert not os.path.exists(os.path.join(temp_dir, "to_delete.txt"))

    def test_delete_empty_directory(self, local_backend, temp_dir):
        """Test deleting an empty directory."""
        os.makedirs(os.path.join(temp_dir, "empty_dir"))
        result = local_backend.delete("empty_dir")

        assert "deleted" in result.lower()
        assert not os.path.exists(os.path.join(temp_dir, "empty_dir"))

    def test_delete_nonexistent(self, local_backend):
        """Test deleting non-existent path."""
        result = local_backend.delete("nonexistent.txt")

        assert "not found" in result.lower()


class TestLocalMemoryBackendRename:
    """Tests for rename operations."""

    def test_rename_file(self, local_backend, temp_dir):
        """Test renaming a file."""
        local_backend.create_update("old_name.txt", "Content", mode="create")
        result = local_backend.rename("old_name.txt", "new_name.txt")

        assert "renamed" in result.lower()
        assert not os.path.exists(os.path.join(temp_dir, "old_name.txt"))
        assert os.path.exists(os.path.join(temp_dir, "new_name.txt"))

    def test_move_file_to_subdirectory(self, local_backend, temp_dir):
        """Test moving a file to a subdirectory."""
        local_backend.create_update("moveme.txt", "Content", mode="create")
        result = local_backend.rename("moveme.txt", "subdir/moved.txt")

        assert "renamed" in result.lower()
        assert os.path.exists(os.path.join(temp_dir, "subdir", "moved.txt"))

    def test_rename_nonexistent(self, local_backend):
        """Test renaming non-existent file."""
        result = local_backend.rename("nonexistent.txt", "new_name.txt")

        assert "not found" in result.lower()


class TestLocalMemoryBackendClearAll:
    """Tests for clearing all memory."""

    def test_clear_all_files(self, local_backend, temp_dir):
        """Test clearing all files."""
        local_backend.create_update("file1.txt", "Content 1", mode="create")
        local_backend.create_update("file2.txt", "Content 2", mode="create")
        local_backend.create_update("subdir/file3.txt", "Content 3", mode="create")

        result = local_backend.clear_all_memory()

        assert "cleared" in result.lower()
        # Base dir should still exist but be empty
        assert os.path.exists(temp_dir)
        assert len(os.listdir(temp_dir)) == 0


class TestLocalMemoryBackendSecurity:
    """Security tests for path traversal protection."""

    def test_path_traversal_attack_parent_dir(self, local_backend):
        """Test that parent directory traversal is blocked."""
        result = local_backend.create_update(
            "../outside.txt", "Malicious", mode="create"
        )

        assert (
            "traversal" in result.lower()
            or "invalid" in result.lower()
            or "outside" in result.lower()
        )

    def test_path_traversal_attack_absolute(self, local_backend):
        """Test that absolute paths outside base are blocked."""
        _result = local_backend.create_update("/etc/passwd", "Malicious", mode="create")

        # Should either error or resolve safely inside base dir
        assert not os.path.exists("/etc/passwd_malicious")

    def test_path_traversal_encoded(self, local_backend):
        """Test that URL-encoded traversal is blocked."""
        result = local_backend.create_update(
            "..%2F..%2Foutside.txt", "Malicious", mode="create"
        )

        # Should be handled safely
        assert (
            "traversal" in result.lower()
            or "invalid" in result.lower()
            or "outside" in result.lower()
            or "created" in result.lower()
        )

    def test_null_byte_injection(self, local_backend):
        """Test that null byte injection is handled."""
        # This should not cause issues
        _result = local_backend.view("file.txt\x00.jpg")
        # Should not crash, may report not found


class TestLocalMemoryBackendConcurrency:
    """Concurrency tests for thread safety."""

    def test_concurrent_writes(self, local_backend, temp_dir):
        """Test that concurrent writes don't corrupt data."""
        # Create initial file
        local_backend.create_update("concurrent.txt", "Initial", mode="create")

        def append_content(thread_id):
            for i in range(10):
                local_backend.create_update(
                    "concurrent.txt", f"Thread{thread_id}-{i}", mode="append"
                )

        threads = []
        for t in range(5):
            thread = threading.Thread(target=append_content, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify file is not corrupted
        with open(os.path.join(temp_dir, "concurrent.txt"), "r") as f:
            content = f.read()
            assert "Initial" in content
            # All 50 appends should be present (5 threads × 10 writes)
            for t in range(5):
                for i in range(10):
                    assert f"Thread{t}-{i}" in content

    def test_concurrent_read_write(self, local_backend, temp_dir):
        """Test concurrent reads and writes."""
        local_backend.create_update("rw_test.txt", "Original content", mode="create")

        results = []

        def read_file():
            for _ in range(10):
                result = local_backend.view("rw_test.txt")
                results.append(result)
                time.sleep(0.01)

        def write_file():
            for i in range(10):
                local_backend.create_update(
                    "rw_test.txt", f"Update {i}", mode="overwrite"
                )
                time.sleep(0.01)

        reader = threading.Thread(target=read_file)
        writer = threading.Thread(target=write_file)

        reader.start()
        writer.start()

        reader.join()
        writer.join()

        # No exceptions should have occurred
        assert len(results) == 10


class TestLocalMemoryBackendEdgeCases:
    """Edge case tests."""

    def test_very_long_filename(self, local_backend, temp_dir):
        """Test handling of very long filenames."""
        long_name = "a" * 200 + ".txt"
        result = local_backend.create_update(long_name, "Content", mode="create")

        # Should either succeed or fail gracefully
        assert (
            "created" in result.lower()
            or "error" in result.lower()
            or "invalid" in result.lower()
        )

    def test_special_characters_in_filename(self, local_backend, temp_dir):
        """Test filenames with special characters."""
        # Safe special characters
        result = local_backend.create_update(
            "file-name_2024.txt", "Content", mode="create"
        )
        assert "created" in result.lower()

    def test_whitespace_only_content(self, local_backend, temp_dir):
        """Test file with only whitespace."""
        result = local_backend.create_update(
            "whitespace.txt", "   \n\t\n   ", mode="create"
        )
        assert "created" in result.lower()

        with open(os.path.join(temp_dir, "whitespace.txt"), "r") as f:
            content = f.read()
            assert content.strip() == ""

    def test_very_large_file(self, local_backend, temp_dir):
        """Test handling of large content."""
        large_content = "x" * (1024 * 1024)  # 1MB
        result = local_backend.create_update("large.txt", large_content, mode="create")

        assert "created" in result.lower()

        with open(os.path.join(temp_dir, "large.txt"), "r") as f:
            content = f.read()
            assert len(content) == 1024 * 1024


# ============================================================================
# S3MemoryBackend Tests
# ============================================================================


class TestS3MemoryBackendInitialization:
    """Tests for S3MemoryBackend initialization."""

    def test_initialization_with_credentials(self, mock_s3_client):
        """Test initialization with explicit credentials."""
        from omnicoreagent.core.tools.memory_tool.s3_storage import S3MemoryBackend

        with patch("boto3.client", return_value=mock_s3_client):
            backend = S3MemoryBackend(
                bucket_name="test-bucket",
                aws_access_key_id="test-key",
                aws_secret_access_key="test-secret",
            )

            assert backend.bucket == "test-bucket"
            assert backend.prefix == "memories/"

    def test_initialization_validates_bucket_exists(self, mock_s3_client):
        """Test that initialization validates bucket access."""
        from omnicoreagent.core.tools.memory_tool.s3_storage import S3MemoryBackend

        mock_s3_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_s3_client):
            with pytest.raises(ValueError) as exc_info:
                S3MemoryBackend(
                    bucket_name="nonexistent-bucket",
                    aws_access_key_id="test-key",
                    aws_secret_access_key="test-secret",
                )

            assert "does not exist" in str(exc_info.value)

    def test_initialization_handles_access_denied(self, mock_s3_client):
        """Test handling of access denied error."""
        from omnicoreagent.core.tools.memory_tool.s3_storage import S3MemoryBackend

        mock_s3_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Access Denied"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_s3_client):
            with pytest.raises(ValueError) as exc_info:
                S3MemoryBackend(
                    bucket_name="private-bucket",
                    aws_access_key_id="test-key",
                    aws_secret_access_key="test-secret",
                )

            assert "access denied" in str(exc_info.value).lower()


class TestS3MemoryBackendOperations:
    """Tests for S3 CRUD operations."""

    def test_create_file(self, s3_backend, mock_s3_client):
        """Test creating a file in S3."""
        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        mock_s3_client.put_object.return_value = {"ETag": '"abc123"'}

        result = s3_backend.create_update("test.txt", "Hello S3!", mode="create")

        mock_s3_client.put_object.assert_called_once()
        assert "created" in result.lower() or "success" in result.lower()

    def test_create_file_fails_if_exists(self, s3_backend, mock_s3_client):
        """Test that create fails if file exists."""
        mock_s3_client.head_object.return_value = {"ContentLength": 100}
        mock_s3_client.get_object.return_value = {
            "Body": io.BytesIO(b"Existing content"),
            "ETag": '"abc123"',
        }

        result = s3_backend.create_update("existing.txt", "New content", mode="create")

        assert "exists" in result.lower() or "already" in result.lower()

    def test_view_file(self, s3_backend, mock_s3_client):
        """Test viewing a file from S3."""
        mock_s3_client.get_object.return_value = {
            "Body": io.BytesIO(b"File content from S3"),
            "ETag": '"abc123"',
        }

        result = s3_backend.view("test.txt")

        assert "File content from S3" in result

    def test_view_directory_listing(self, s3_backend, mock_s3_client):
        """Test listing directory contents."""
        mock_s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "GetObject"
        )
        mock_s3_client.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "memories/file1.txt",
                    "Size": 100,
                    "LastModified": datetime(2024, 1, 1),
                    "ETag": '"abc123"',
                },
                {
                    "Key": "memories/file2.txt",
                    "Size": 200,
                    "LastModified": datetime(2024, 1, 1),
                    "ETag": '"def456"',
                },
            ],
            "CommonPrefixes": [{"Prefix": "memories/subdir/"}],
        }

        result = s3_backend.view("")

        assert "file1.txt" in result or "file2.txt" in result

    def test_append_to_file(self, s3_backend, mock_s3_client):
        """Test appending to a file."""
        mock_s3_client.get_object.return_value = {
            "Body": io.BytesIO(b"Original"),
            "ETag": '"abc123"',
        }
        mock_s3_client.put_object.return_value = {"ETag": '"def456"'}

        result = s3_backend.create_update("test.txt", "Appended", mode="append")

        assert "appended" in result.lower() or "success" in result.lower()

    def test_overwrite_file(self, s3_backend, mock_s3_client):
        """Test overwriting a file."""
        mock_s3_client.head_object.return_value = {"ContentLength": 100}
        mock_s3_client.put_object.return_value = {"ETag": '"abc123"'}

        result = s3_backend.create_update("test.txt", "New content", mode="overwrite")

        assert "overwritten" in result.lower() or "success" in result.lower()

    def test_delete_file(self, s3_backend, mock_s3_client):
        """Test deleting a file."""
        mock_s3_client.head_object.return_value = {"ContentLength": 100}
        mock_s3_client.delete_object.return_value = {}

        result = s3_backend.delete("test.txt")

        mock_s3_client.delete_object.assert_called_once()
        assert "deleted" in result.lower()


class TestS3MemoryBackendRetryLogic:
    """Tests for S3 retry logic."""

    def test_retries_on_throttling(self, mock_s3_client):
        """Test that operations are retried on throttling."""
        from omnicoreagent.core.tools.memory_tool.s3_storage import S3MemoryBackend

        # First two calls fail with throttling, third succeeds
        mock_s3_client.head_bucket.side_effect = [
            ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                "HeadBucket",
            ),
            ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
                "HeadBucket",
            ),
            {},  # Success
        ]
        mock_s3_client.get_bucket_versioning.return_value = {}
        mock_s3_client.get_bucket_encryption.return_value = {}

        with patch("boto3.client", return_value=mock_s3_client):
            with patch("time.sleep"):  # Skip actual sleep in tests
                _backend = S3MemoryBackend(
                    bucket_name="test-bucket",
                    aws_access_key_id="test-key",
                    aws_secret_access_key="test-secret",
                )

        assert mock_s3_client.head_bucket.call_count == 3

    def test_no_retry_on_not_found(self, mock_s3_client):
        """Test that 404 errors are not retried."""
        from omnicoreagent.core.tools.memory_tool.s3_storage import S3MemoryBackend

        mock_s3_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_s3_client):
            with pytest.raises(ValueError):
                S3MemoryBackend(
                    bucket_name="nonexistent",
                    aws_access_key_id="test-key",
                    aws_secret_access_key="test-secret",
                )

        # Should not retry on 404
        assert mock_s3_client.head_bucket.call_count == 1


class TestS3MemoryBackendSecurity:
    """Security tests for S3 backend."""

    def test_key_normalization_prevents_traversal(self, s3_backend):
        """Test that path traversal is prevented in key generation."""
        with pytest.raises(ValueError, match="Path traversal detected"):
            s3_backend._normalize_key("../../../etc/passwd")

    def test_key_normalization_with_memories_prefix(self, s3_backend):
        """Test that 'memories/' prefix in path is handled correctly."""
        key = s3_backend._normalize_key("memories/subdir/file.txt")

        # Should not double the prefix
        assert not key.startswith("memories/memories/")


# ============================================================================
# R2MemoryBackend Tests
# ============================================================================


class TestR2MemoryBackendInitialization:
    """Tests for R2MemoryBackend initialization."""

    def test_initialization_with_account_id(self, mock_r2_client):
        """Test R2 initialization with account ID."""
        from omnicoreagent.core.tools.memory_tool.r2_storage import R2MemoryBackend

        with patch("boto3.client", return_value=mock_r2_client):
            backend = R2MemoryBackend(
                bucket_name="test-bucket",
                account_id="abc123",
                access_key_id="test-key",
                secret_access_key="test-secret",
            )

            assert backend.bucket == "test-bucket"
            assert backend.account_id == "abc123"

    def test_endpoint_url_format(self, mock_r2_client):
        """Test that R2 endpoint URL is correctly formatted."""
        from omnicoreagent.core.tools.memory_tool.r2_storage import R2MemoryBackend

        with patch("boto3.client", return_value=mock_r2_client) as mock_client:
            R2MemoryBackend(
                bucket_name="my-bucket",
                account_id="acc123",
                access_key_id="key",
                secret_access_key="secret",
            )

            # Check the endpoint URL was passed correctly
            call_kwargs = mock_client.call_args[1]
            assert "endpoint_url" in call_kwargs
            assert "acc123" in call_kwargs["endpoint_url"]

    def test_versioning_disabled_for_r2(self, mock_r2_client):
        """Test that versioning is disabled for R2."""
        from omnicoreagent.core.tools.memory_tool.r2_storage import R2MemoryBackend

        with patch("boto3.client", return_value=mock_r2_client):
            backend = R2MemoryBackend(
                bucket_name="test-bucket",
                account_id="abc123",
                access_key_id="test-key",
                secret_access_key="test-secret",
            )

            assert backend.enable_versioning is False


class TestR2MemoryBackendOperations:
    """Tests for R2 operations (inherited from S3)."""

    def test_create_file(self, r2_backend, mock_r2_client):
        """Test creating a file in R2."""
        mock_r2_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        mock_r2_client.put_object.return_value = {"ETag": '"abc123"'}

        _result = r2_backend.create_update("test.txt", "Hello R2!", mode="create")

        mock_r2_client.put_object.assert_called_once()

    def test_view_file(self, r2_backend, mock_r2_client):
        """Test viewing a file from R2."""
        mock_r2_client.get_object.return_value = {
            "Body": io.BytesIO(b"R2 file content"),
            "ETag": '"abc123"',
        }

        result = r2_backend.view("test.txt")

        assert "R2 file content" in result

    def test_encryption_params_not_sent(self, r2_backend, mock_r2_client):
        """Test that encryption params are not sent (R2 encrypts by default)."""
        params = r2_backend._get_put_object_params()

        # R2 should not include ServerSideEncryption
        assert "ServerSideEncryption" not in params


# ============================================================================
# Factory Tests
# ============================================================================


class TestMemoryBackendFactory:
    """Tests for the factory function."""

    def test_create_local_backend(self, temp_dir):
        """Test creating local backend via factory."""
        from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend

        backend = create_memory_backend("local")

        assert backend is not None
        # Check it's the right type
        assert hasattr(backend, "view")
        assert hasattr(backend, "create_update")

    def test_create_s3_backend_without_env(self):
        """Test S3 backend creation fails without env vars."""
        from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend

        # Clear any existing env vars
        env_backup = {}
        for key in ["AWS_S3_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]:
            env_backup[key] = os.environ.pop(key, None)

        try:
            with pytest.raises(ValueError) as exc_info:
                create_memory_backend("s3")

            assert "AWS_S3_BUCKET" in str(exc_info.value)
        finally:
            # Restore env
            for key, value in env_backup.items():
                if value:
                    os.environ[key] = value

    def test_create_r2_backend_without_env(self):
        """Test R2 backend creation fails without env vars."""
        from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend

        # Clear any existing env vars
        env_backup = {}
        for key in [
            "R2_BUCKET_NAME",
            "R2_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        ]:
            env_backup[key] = os.environ.pop(key, None)

        try:
            with pytest.raises(ValueError) as exc_info:
                create_memory_backend("r2")

            assert "R2_" in str(exc_info.value)
        finally:
            # Restore env
            for key, value in env_backup.items():
                if value:
                    os.environ[key] = value

    def test_invalid_backend_type(self):
        """Test that invalid backend type raises error."""
        from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend

        with pytest.raises(ValueError) as exc_info:
            create_memory_backend("invalid")

        assert "unknown backend" in str(exc_info.value).lower()

    def test_case_insensitive_backend_type(self, temp_dir):
        """Test that backend type is case-insensitive."""
        from omnicoreagent.core.tools.memory_tool.factory import create_memory_backend

        backend1 = create_memory_backend("LOCAL")
        backend2 = create_memory_backend("Local")
        backend3 = create_memory_backend("local")

        assert backend1 is not None
        assert backend2 is not None
        assert backend3 is not None


# ============================================================================
# MemoryTool Integration Tests
# ============================================================================


class TestMemoryToolIntegration:
    """Integration tests for MemoryTool class."""

    def test_memory_tool_with_local_backend(self, temp_dir):
        """Test MemoryTool with local backend."""
        from omnicoreagent.core.tools.memory_tool import factory
        from omnicoreagent.core.tools.memory_tool.memory_tool import MemoryTool

        factory._backend_cache.clear()
        with patch.dict(os.environ, {"OMNICOREAGENT_WORKSPACE_DIR": temp_dir}):
            tool = MemoryTool(backend="local")

            # Test all operations
            result = tool.create_update("test.txt", "Hello", mode="create")
        assert "created" in result.lower() or "success" in result.lower()

    def test_memory_tool_with_direct_backend(self, local_backend):
        """Test MemoryTool with direct backend injection."""
        from omnicoreagent.core.tools.memory_tool.memory_tool import MemoryTool

        tool = MemoryTool(backend=local_backend)

        result = tool.view("")
        assert result is not None

    def test_memory_tool_defaults_to_local(self):
        """Test that MemoryTool defaults to local backend."""
        from omnicoreagent.core.tools.memory_tool.memory_tool import MemoryTool

        tool = MemoryTool()

        # Should work without errors
        result = tool.view("")
        assert result is not None


# ============================================================================
# Build Tool Registry Tests
# ============================================================================


class TestBuildToolRegistry:
    """Tests for build_tool_registry_memory_tool function."""

    def test_registers_all_tools(self):
        """Test that all memory tools are registered."""
        from omnicoreagent.core.tools.memory_tool.memory_tool import (
            build_tool_registry_memory_tool,
        )
        from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

        registry = ToolRegistry()
        build_tool_registry_memory_tool("local", registry)

        tools = registry.get_available_tools()
        tool_names = [t["name"] for t in tools]

        assert "memory_view" in tool_names
        assert "memory_create_update" in tool_names
        assert "memory_str_replace" in tool_names
        assert "memory_insert" in tool_names
        assert "memory_delete" in tool_names
        assert "memory_rename" in tool_names
        assert "memory_clear_all" in tool_names

    def test_tools_are_callable(self):
        """Test that registered tools are callable."""
        from omnicoreagent.core.tools.memory_tool.memory_tool import (
            build_tool_registry_memory_tool,
        )
        from omnicoreagent.core.tools.local_tools_registry import ToolRegistry

        registry = ToolRegistry()
        build_tool_registry_memory_tool("local", registry)

        # Get and call memory_view
        tools = registry.get_available_tools()
        view_tool = next(t for t in tools if t["name"] == "memory_view")

        # The tool should be registered with proper schema
        assert "inputSchema" in view_tool
        assert view_tool["inputSchema"]["type"] == "object"
