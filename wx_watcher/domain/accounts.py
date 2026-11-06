"""Account identity, configuration readiness and live-monitoring rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountConnection:
    current_root: str | None
    selected_root: str | None
    active_count: int
    credential_saved: bool
    data_available: bool
    access_denied: bool = False

    @property
    def matches_current(self) -> bool:
        return bool(
            self.current_root
            and self.selected_root
            and self.current_root == self.selected_root
            and self.active_count == 1
        )

    @property
    def can_configure(self) -> bool:
        return bool(
            not self.access_denied
            and self.selected_root
            and self.credential_saved
            and self.data_available
            and (self.matches_current or self.active_count == 0)
        )


def require_account_match(selected: str, active: list[str], *, live: bool) -> None:
    if len(active) > 1:
        raise ValueError("识别到多个登录账号，请确认要使用的账号后刷新")
    if not active and live:
        raise ValueError(
            "尚未确认微信当前登录账号。请正常登录并在账号区域刷新；若已登录，请检查完整磁盘访问权限。"
        )
    if active and active[0] != selected:
        raise ValueError(
            "微信当前登录账号与本项目选择的账号不同。请在账号区域连接或切换当前账号，不能沿用旧账号配置。"
        )
