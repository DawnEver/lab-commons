"""THE CLAIMING ACCOUNT is read from Slurm and the login, never typed."""

from __future__ import annotations

from lab_commons.hpc.account import ClusterAccount, cluster_account


def test_the_account_is_the_slurm_cluster_and_the_login_user() -> None:
    asked: list[str] = []

    def shell(command: str, _stdin: str | None) -> str:
        asked.append(command)
        return 'ada\nuser-a\n'

    found = cluster_account(shell)
    assert found == ClusterAccount('ada', 'user-a')
    assert str(found) == 'ada/user-a'
    assert 'ClusterName' in asked[0]
    assert 'id -un' in asked[0]


def test_a_box_without_slurm_has_no_account() -> None:
    """PLANTED CONTROL: the same call answered by a workstation (no sbatch: nothing printed) or no shell."""
    assert cluster_account(lambda _c, _s: '') is None

    def no_shell(_command: str, _stdin: str | None) -> str:
        msg = 'bash'
        raise FileNotFoundError(msg)

    assert cluster_account(no_shell) is None
