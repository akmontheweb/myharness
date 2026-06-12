"""
Tests for the task dispatcher module.
"""

import os

import pytest

from harness.task_dispatcher import TaskDispatcher, TaskError


@pytest.fixture
def temp_work_dir(tmp_path):
    """Provide a clean temporary working directory."""
    return str(tmp_path)


class TestTaskError:
    """Tests for the TaskError exception."""

    def test_default_attributes_are_none(self):
        exc = TaskError("something went wrong")
        assert str(exc) == "something went wrong"
        assert exc.returncode is None
        assert exc.stdout is None
        assert exc.stderr is None

    def test_all_attributes_set(self):
        exc = TaskError(
            "failure",
            returncode=1,
            stdout="output",
            stderr="error log",
        )
        assert exc.returncode == 1
        assert exc.stdout == "output"
        assert exc.stderr == "error log"

    def test_isinstance_exception(self):
        exc = TaskError("oops")
        assert isinstance(exc, Exception)
        with pytest.raises(TaskError, match="oops"):
            raise exc


class TestRunTask:
    """Tests for TaskDispatcher.run_task async method."""

    @pytest.mark.asyncio
    async def test_successful_string_command(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        result = await dispatcher.run_task("echo hello")
        assert result.returncode == 0
        assert result.stdout == b"hello\n"
        assert result.stderr == b""

    @pytest.mark.asyncio
    async def test_successful_list_command(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        result = await dispatcher.run_task(["echo", "hello"])
        assert result.returncode == 0
        assert result.stdout == b"hello\n"
        assert result.stderr == b""

    @pytest.mark.asyncio
    async def test_exit_non_zero_raises_task_error(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        with pytest.raises(TaskError, match=r"Task failed \(exit 42\)"):
            await dispatcher.run_task(
                "python3 -c 'import sys; sys.exit(42)'"
            )

    @pytest.mark.asyncio
    async def test_exit_non_zero_captures_stdout_stderr(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        try:
            await dispatcher.run_task(
                "python3 -c 'import sys; print(\"out\"); print(\"err\", file=sys.stderr); sys.exit(1)'"
            )
        except TaskError as exc:
            assert exc.returncode == 1
            assert "out" in exc.stdout
            assert "err" in exc.stderr
        else:
            pytest.fail("Expected TaskError")

    @pytest.mark.asyncio
    async def test_timeout_raises_task_error(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir, timeout=0.2)
        with pytest.raises(TaskError, match="timed out after 0.2s"):
            await dispatcher.run_task("sleep 2")

    @pytest.mark.asyncio
    async def test_command_not_found_raises_task_error(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        with pytest.raises(TaskError):
            await dispatcher.run_task("nonexistent_command_xyz")

    @pytest.mark.asyncio
    async def test_custom_environment(self, temp_work_dir):
        env = {"MY_VAR": "custom_value"}
        dispatcher = TaskDispatcher(work_dir=temp_work_dir, env=env)
        result = await dispatcher.run_task("echo $MY_VAR")
        assert result.returncode == 0
        assert result.stdout.decode().strip() == "custom_value"

    @pytest.mark.asyncio
    async def test_work_dir_is_used(self, tmp_path):
        """Verify the command runs in the provided work_dir."""
        dispatcher = TaskDispatcher(work_dir=str(tmp_path))
        # Create a file there via the dispatcher
        await dispatcher.run_task("touch testfile")
        assert (tmp_path / "testfile").exists()

    @pytest.mark.asyncio
    async def test_inherits_current_work_dir_by_default(self):
        """If work_dir is not given, it should use cwd."""
        dispatcher = TaskDispatcher()
        result = await dispatcher.run_task("pwd")
        # The output is the current working directory (where tests run)
        assert result.stdout.decode().strip() == os.getcwd()


class TestRunParallel:
    """Tests for TaskDispatcher.run_parallel method."""

    @pytest.mark.asyncio
    async def test_all_successful(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        tasks = ["echo one", "echo two", "echo three"]
        results = await dispatcher.run_parallel(tasks, max_concurrent=2)
        assert len(results) == 3
        outputs = [r.stdout.decode().strip() for r in results]
        assert outputs == ["one", "two", "three"]

    @pytest.mark.asyncio
    async def test_first_failure_raises_task_error(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        tasks = [
            "echo ok1",
            "python3 -c 'import sys; sys.exit(1)'",
            "echo ok3",
        ]
        with pytest.raises(TaskError, match="Task 1 failed"):
            await dispatcher.run_parallel(tasks, max_concurrent=2)

    @pytest.mark.asyncio
    async def test_empty_tasks_list_returns_empty(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        results = await dispatcher.run_parallel([])
        assert results == []


class TestRunTaskSync:
    """Tests for the synchronous wrapper run_task_sync."""

    def test_sync_success(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        result = dispatcher.run_task_sync("echo sync")
        assert result.returncode == 0
        assert result.stdout.strip() == b"sync"

    def test_sync_failure(self, temp_work_dir):
        dispatcher = TaskDispatcher(work_dir=temp_work_dir)
        with pytest.raises(TaskError, match="Task failed"):
            dispatcher.run_task_sync("exit 1")
