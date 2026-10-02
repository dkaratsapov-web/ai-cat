"""Контроль расходов: бюджет месяца, лимит на ролик, подтверждение платных операций."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import Settings
from ..db import DB


class BudgetError(RuntimeError):
    pass


@dataclass
class CostLine:
    scene_id: str
    provider: str
    kind: str
    model: str
    detail: str
    usd: float
    reused: bool = False       # результат уже есть — платить не нужно
    verified_price: bool = True


@dataclass
class Estimate:
    episode: str
    lines: list[CostLine] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(l.usd for l in self.lines if not l.reused), 4)

    def table(self) -> str:
        rows = [f"{'Сцена':<6} {'Сервис':<11} {'Операция':<12} {'Параметры':<34} {'$':>8}"]
        for l in self.lines:
            mark = " (готово, повторно не оплачивается)" if l.reused else ("" if l.verified_price else " *")
            rows.append(f"{l.scene_id:<6} {l.provider:<11} {l.kind:<12} {l.detail[:34]:<34} {l.usd:>8.3f}{mark}")
        rows.append(f"{'':<6} {'':<11} {'':<12} {'ИТОГО к оплате (оценка)':<34} {self.total:>8.3f}")
        if any(not l.verified_price for l in self.lines):
            rows.append("* цена из неофициального источника — сверьте в личном кабинете")
        return "\n".join(rows)


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class Budget:
    def __init__(self, settings: Settings, db: DB):
        self.s = settings
        self.db = db

    @property
    def monthly(self) -> float:
        return float(self.s.get("budget.monthly_usd", 75))

    @property
    def per_video(self) -> float:
        return float(self.s.get("budget.per_video_usd", 6))

    def status(self, episode: str | None = None) -> dict:
        spent_month = self.db.spend(month=current_month())
        out = {
            "month": current_month(), "spent_month_usd": spent_month, "monthly_budget_usd": self.monthly,
            "left_month_usd": round(self.monthly - spent_month, 4),
        }
        if episode:
            spent_ep = self.db.spend(episode=episode)
            out.update({"spent_episode_usd": spent_ep, "per_video_limit_usd": self.per_video,
                        "left_episode_usd": round(self.per_video - spent_ep, 4)})
        return out

    def check(self, episode: str, amount: float) -> list[str]:
        """Проверяет лимиты; бросает BudgetError при превышении, возвращает предупреждения."""
        st = self.status(episode)
        warnings: list[str] = []
        if st["spent_month_usd"] + amount > self.monthly:
            raise BudgetError(
                f"Превышение месячного бюджета: потрачено ${st['spent_month_usd']:.2f} + ${amount:.2f} > ${self.monthly:.2f}. "
                "Увеличьте budget.monthly_usd в config/studio.yaml, если это осознанно."
            )
        if st["spent_episode_usd"] + amount > self.per_video:
            raise BudgetError(
                f"Превышение лимита на ролик: ${st['spent_episode_usd']:.2f} + ${amount:.2f} > ${self.per_video:.2f}."
            )
        ratio = float(self.s.get("budget.warn_ratio", 0.8))
        if (st["spent_month_usd"] + amount) >= self.monthly * ratio:
            warnings.append(f"Внимание: после операции расход месяца составит "
                            f"${st['spent_month_usd'] + amount:.2f} из ${self.monthly:.2f}")
        return warnings

    def confirm(self, prompt: str, assume_yes: bool = False) -> bool:
        if assume_yes:
            return True
        if not self.s.get("budget.require_confirmation", True):
            return True
        if not sys.stdin.isatty():
            print("Требуется подтверждение платной операции. Повторите с флагом --yes после проверки сметы.")
            return False
        ans = input(f"{prompt} Введите 'да' для подтверждения: ").strip().lower()
        return ans in ("да", "yes", "y", "д")
