import platform
import tempfile
from pathlib import Path

import pytest

from autosentry_agent.mcp.credential_file import (
    CredentialFile,
    sweep_stale_credential_files,
)


class TestCredentialFile:
    """Tests for the CredentialFile context manager."""

    def test_creates_file_with_correct_content(self):
        """CredentialFile creates a file with the correct content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            tenant_id = "test-tenant"
            credential_value = "my-secret-password"

            with CredentialFile(tenant_id, credential_value, base_dir) as path:
                # Verify file exists
                assert path.exists()
                # Verify content
                assert path.read_text(encoding="utf-8") == credential_value
                # Verify file is in base_dir
                assert path.parent == base_dir

            # Verify file is deleted after context exit
            assert not path.exists()

    def test_yields_path_with_tenant_id_prefix(self):
        """CredentialFile filename includes the tenant_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            tenant_id = "acme-corp"

            with CredentialFile(tenant_id, "secret", base_dir) as path:
                assert path.name.startswith(f"{tenant_id}-")

    def test_creates_directory_if_not_exists(self):
        """CredentialFile creates base_dir if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "does" / "not" / "exist"
            assert not base_dir.exists()

            with CredentialFile("tenant", "secret", base_dir) as path:
                assert path.exists()
                assert base_dir.exists()

    def test_directory_permissions_are_700(self):
        """CredentialFile creates base_dir with mode 0o700 (or Windows equivalent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "credentials"

            with CredentialFile("tenant", "secret", base_dir) as path:
                mode_bits = base_dir.stat().st_mode & 0o777
                # On Unix-like systems, verify 0o700. On Windows, the mode is less
                # restrictive due to ACL-based permissions, but the code attempts
                # to set it. Just verify the directory exists and was created.
                if platform.system() != "Windows":
                    assert mode_bits == 0o700
                assert base_dir.exists()
                assert path.exists()

    def test_file_permissions_are_600(self):
        """CredentialFile creates files with mode 0o600 (or Windows equivalent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            with CredentialFile("tenant", "secret", base_dir) as path:
                mode_bits = path.stat().st_mode & 0o777
                # On Unix-like systems, verify 0o600. On Windows, the mode is less
                # restrictive due to ACL-based permissions, but the code attempts
                # to set it. Just verify the file was created with the right content.
                if platform.system() != "Windows":
                    assert mode_bits == 0o600
                assert path.exists()
                assert path.read_text(encoding="utf-8") == "secret"

    def test_cleanup_on_exception(self):
        """CredentialFile deletes file even if exception is raised in with block."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            file_path = None

            with pytest.raises(ValueError, match="test error"):
                with CredentialFile("tenant", "secret", base_dir) as path:
                    file_path = path
                    assert path.exists()
                    raise ValueError("test error")

            # Verify file was deleted despite exception
            assert not file_path.exists()

    def test_multiple_files_have_different_names(self):
        """CredentialFile uses random hex to ensure unique filenames."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            filenames = set()

            # Create several files and collect their names
            for _ in range(5):
                with CredentialFile("tenant", "secret", base_dir) as path:
                    filenames.add(path.name)

            # All filenames should be unique
            assert len(filenames) == 5

    def test_handles_utf8_content(self):
        """CredentialFile correctly encodes and stores UTF-8 content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            # Use unicode characters
            secret = "password-with-unicode-@@@-and-emoji-🔐"

            with CredentialFile("tenant", secret, base_dir) as path:
                content = path.read_text(encoding="utf-8")
                assert content == secret

    def test_preserves_embedded_newlines_byte_for_byte(self):
        """CredentialFile must not let Windows text-mode translation mangle
        secret values containing \\n or \\r\\n. Read back in binary mode so a
        text-mode round trip (which normalizes on read too) cannot hide the
        bug."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            secret = "line1\nline2\r\nline3"

            with CredentialFile("tenant", secret, base_dir) as path:
                with open(path, "rb") as f:
                    raw_bytes = f.read()

                assert raw_bytes == secret.encode("utf-8")

    def test_existing_directory_is_not_recreated(self):
        """CredentialFile works with an already-existing directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "creds"
            base_dir.mkdir(mode=0o700)

            with CredentialFile("tenant", "secret", base_dir) as path:
                assert path.exists()
                # Verify directory still exists and wasn't recreated
                assert base_dir.exists()

    def test_cleanup_with_missing_file_does_not_raise(self):
        """CredentialFile cleanup handles missing files gracefully (missing_ok=True)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            with CredentialFile("tenant", "secret", base_dir) as path:
                file_to_track = path
                # Manually delete the file before context exit
                path.unlink()

            # Context exit should not raise, because missing_ok=True
            assert not file_to_track.exists()

    def test_empty_secret_value(self):
        """CredentialFile handles empty string secrets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            with CredentialFile("tenant", "", base_dir) as path:
                assert path.read_text(encoding="utf-8") == ""
                assert path.exists()

            assert not path.exists()

    @pytest.mark.parametrize(
        "tenant_id",
        [
            "../escape",
            "..",
            "a/b",
            "a\\b",
            "",
            "tenant id",
            "tenant.id",
            "tenant\n",
            "C:evil",
        ],
    )
    def test_rejects_unsafe_tenant_id_before_touching_the_filesystem(self, tenant_id):
        """tenant_id becomes part of the filename, so anything outside
        ^[A-Za-z0-9_-]+$ is rejected with a ValueError, and no directory
        or file is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "creds"

            with pytest.raises(ValueError, match="Invalid tenant_id"):
                with CredentialFile(tenant_id, "secret", base_dir):
                    pytest.fail("body must not run for an invalid tenant_id")

            assert not base_dir.exists()

    @pytest.mark.parametrize("tenant_id", ["acme", "acme-corp", "tenant_42", "A-Z_0-9"])
    def test_accepts_safe_tenant_ids(self, tenant_id):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            with CredentialFile(tenant_id, "secret", base_dir) as path:
                assert path.parent == base_dir
                assert path.name.startswith(f"{tenant_id}-")


class TestSweepStaleCredentialFiles:
    """Tests for the sweep_stale_credential_files function."""

    def test_deletes_all_files_in_directory(self):
        """sweep_stale_credential_files deletes all files in the directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "creds"
            base_dir.mkdir()

            # Create some files
            file1 = base_dir / "file1.txt"
            file2 = base_dir / "file2.txt"
            file3 = base_dir / "file3.txt"
            file1.write_text("content1")
            file2.write_text("content2")
            file3.write_text("content3")

            # Create a subdirectory (should not be deleted, only files)
            subdir = base_dir / "subdir"
            subdir.mkdir()

            assert file1.exists()
            assert file2.exists()
            assert file3.exists()

            sweep_stale_credential_files(base_dir)

            # All files should be deleted
            assert not file1.exists()
            assert not file2.exists()
            assert not file3.exists()
            # Subdirectory should still exist
            assert subdir.exists()

    def test_handles_nonexistent_directory(self):
        """sweep_stale_credential_files returns gracefully if directory doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "does" / "not" / "exist"
            # Should not raise
            sweep_stale_credential_files(base_dir)

    def test_only_deletes_files_not_directories(self):
        """sweep_stale_credential_files only deletes files, not subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "creds"
            base_dir.mkdir()

            # Create files and directories
            file1 = base_dir / "file1.txt"
            file1.write_text("content")
            subdir = base_dir / "subdir"
            subdir.mkdir()
            nested_file = subdir / "nested.txt"
            nested_file.write_text("nested content")

            sweep_stale_credential_files(base_dir)

            # File should be deleted, but directories should remain
            assert not file1.exists()
            assert subdir.exists()
            assert nested_file.exists()  # Not recursively deleted

    def test_file_removed_concurrently_does_not_raise(self, monkeypatch):
        """If a file disappears between iterdir()/is_file() and unlink()
        (e.g. its CredentialFile context exits concurrently), the sweep
        must not raise FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "creds"
            base_dir.mkdir()
            racing = base_dir / "tenant-0-deadbeef"
            racing.write_text("orphaned")

            real_is_file = Path.is_file

            def is_file_then_vanish(self):
                result = real_is_file(self)
                if self == racing:
                    racing.unlink()
                return result

            monkeypatch.setattr(Path, "is_file", is_file_then_vanish)

            sweep_stale_credential_files(base_dir)  # must not raise

            assert not racing.exists()

    def test_empty_directory(self):
        """sweep_stale_credential_files works on an empty directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "empty"
            base_dir.mkdir()

            # Should not raise
            sweep_stale_credential_files(base_dir)
            # Directory should still exist
            assert base_dir.exists()

    def test_sweep_credential_files_created_by_credential_file(self):
        """sweep_stale_credential_files cleans up files created by CredentialFile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "creds"

            # Create multiple credential files (without cleanup)
            paths = []
            for i in range(3):
                with CredentialFile(f"tenant-{i}", f"secret-{i}", base_dir) as path:
                    paths.append(path)
                    assert path.exists()

            # Files are cleaned up by the context manager
            for path in paths:
                assert not path.exists()

            # Create more files and leave them there by writing directly
            file1 = base_dir / "tenant-0-abcd1234"
            file2 = base_dir / "tenant-1-efgh5678"
            file1.write_text("orphaned1")
            file2.write_text("orphaned2")

            assert file1.exists()
            assert file2.exists()

            # Sweep should delete them
            sweep_stale_credential_files(base_dir)

            assert not file1.exists()
            assert not file2.exists()
