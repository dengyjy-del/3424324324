"""Жалобы и действия модератора.

Действия админа над анкетой:
  delete  — удалить анкету, владельцу приходит уведомление;
  hide24  — скрыть на 24 часа; после этого анкета не вернётся сама,
            владельцу придётся загрузить новое фото;
  reject  — жалоба отклонена, анкета остаётся в ленте;
  ban     — доступ к режиму закрыт совсем.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config, db, photos

REASONS: dict[str, str] = {
    "minor": "На фото несовершеннолетний",
    "not_own": "Чужое фото / не свой снимок",
    "nsfw": "Нагота или сексуализированный контент",
    "violence": "Насилие, угрозы, ненависть",
    "ad": "Реклама, спам, мошенничество",
    "personal": "Чужие персональные данные",
    "other": "Другое",
}


@dataclass
class ReportResult:
    report_id: int
    autohidden: bool
    snapshot: dict


async def file_report(
    reporter_id: int,
    kind: str,
    target_id: int,
    reason: str,
    comment: str | None = None,
) -> ReportResult:
    """Регистрирует жалобу и при необходимости прячет анкету до решения."""
    if reason not in REASONS:
        reason = "other"

    snapshot = await _snapshot(kind, target_id)
    report_id = await db.add_report(reporter_id, kind, target_id, reason, comment, snapshot)

    autohidden = False
    if kind == "live":
        distinct = await db.open_reports_count(kind, target_id)
        # Жалоба на несовершеннолетнего прячет анкету немедленно,
        # не дожидаясь порога: цена ошибки здесь несимметрична.
        if reason == "minor" or distinct >= config.AUTOHIDE_REPORTS:
            prof = await db.get_profile(target_id)
            if prof and prof["status"] == "active":
                await db.set_status(target_id, "hidden", hidden_until=0)
                autohidden = True

    return ReportResult(report_id=report_id, autohidden=autohidden, snapshot=snapshot)


async def _snapshot(kind: str, target_id: int) -> dict:
    """Фиксирует анкету в том виде, в каком её увидел жалующийся."""
    if kind == "live":
        prof = await db.get_profile(target_id)
        if not prof:
            return {"kind": kind, "id": target_id, "missing": True}
        return {
            "kind": "live",
            "id": target_id,
            "name": prof["display_name"],
            "age": prof["age"],
            "photo_path": prof["photo_path"],
            "photo_file_id": prof["photo_file_id"],
            "status": prof["status"],
            "votes": prof["votes_count"],
        }
    seed = await db.get_seed(target_id)
    if not seed:
        return {"kind": kind, "id": target_id, "missing": True}
    return {
        "kind": "seed",
        "id": target_id,
        "name": seed["display_name"],
        "age": seed["age"],
        "file_name": seed["file_name"],
        "source": seed["source"],
    }


# --- Действия админа ------------------------------------------------------

@dataclass
class ActionResult:
    ok: bool
    text: str                       # что показать админу
    notify_user_id: int | None = None
    notify_text: str | None = None  # что отправить владельцу анкеты


async def apply_action(report_id: int, admin_id: int, action: str) -> ActionResult:
    report = await db.get_report(report_id)
    if report is None:
        return ActionResult(False, "Жалоба не найдена.")

    kind = report["target_kind"]
    target_id = int(report["target_id"])

    if kind == "seed":
        return await _apply_to_seed(report_id, admin_id, action, target_id)
    return await _apply_to_live(report_id, admin_id, action, target_id, report)


async def _apply_to_live(
    report_id: int, admin_id: int, action: str, user_id: int, report: dict
) -> ActionResult:
    prof = await db.get_profile(user_id)
    if prof is None:
        await db.resolve_report(report_id, admin_id, "gone")
        return ActionResult(False, "Анкета уже удалена.")

    # Второй модератор не должен воскресить уже удалённую анкету,
    # нажав «Скрыть на 24ч» на старой карточке жалобы.
    if prof["status"] in {"deleted", "banned"} and action != "reject":
        await db.resolve_reports_for("live", user_id, admin_id, "already_handled")
        already = "удалена" if prof["status"] == "deleted" else "заблокирована"
        return ActionResult(False, f"Анкета уже {already} — решение принято ранее.")

    if action == "delete":
        photos.remove(prof["photo_path"])
        await db.delete_profile(user_id)
        await db.resolve_reports_for("live", user_id, admin_id, "delete")
        await db.log_action(admin_id, "live", user_id, "delete", f"report#{report_id}")
        return ActionResult(
            True,
            f"Анкета {user_id} удалена.",
            notify_user_id=user_id,
            notify_text=(
                "Ваша анкета в режиме оценок удалена модератором: она нарушала правила сервиса.\n\n"
                "Вы можете создать анкету заново, если готовы соблюдать правила. "
                f"Вопросы по решению — {config.SUPPORT_HANDLE}."
            ),
        )

    if action == "hide24":
        until = db.now() + config.HIDE_HOURS * 3600
        await db.set_status(user_id, "hidden", hidden_until=until, needs_reupload=1)
        await db.resolve_reports_for("live", user_id, admin_id, "hide24")
        await db.log_action(admin_id, "live", user_id, "hide24", f"report#{report_id}")
        return ActionResult(
            True,
            f"Анкета {user_id} скрыта на {config.HIDE_HOURS} ч.",
            notify_user_id=user_id,
            notify_text=(
                f"Ваша анкета скрыта на {config.HIDE_HOURS} часа по жалобе — фото не соответствует правилам.\n\n"
                "Через сутки анкета не вернётся в показ автоматически: "
                "чтобы продолжить, загрузите новое фото — это будет предложено при следующем входе.\n\n"
                f"Вопросы по решению — {config.SUPPORT_HANDLE}."
            ),
        )

    if action == "ban":
        photos.remove(prof["photo_path"])
        await db.delete_profile(user_id)
        await db.set_status(user_id, "banned")
        await db.resolve_reports_for("live", user_id, admin_id, "ban")
        await db.log_action(admin_id, "live", user_id, "ban", f"report#{report_id}")
        return ActionResult(
            True,
            f"Пользователь {user_id} заблокирован в режиме оценок.",
            notify_user_id=user_id,
            notify_text=(
                "Доступ к режиму оценок закрыт из-за нарушения правил сервиса.\n\n"
                f"Обжаловать решение — {config.SUPPORT_HANDLE}."
            ),
        )

    if action == "reject":
        # Возвращаем в показ только то, что мы же и спрятали автоматически.
        if prof["status"] == "hidden" and not prof["hidden_until"]:
            await db.set_status(user_id, "active", needs_reupload=0)
        await db.resolve_report(report_id, admin_id, "reject")
        await db.log_action(admin_id, "live", user_id, "reject", f"report#{report_id}")
        return ActionResult(True, "Жалоба отклонена, анкета остаётся в показе.")

    return ActionResult(False, f"Неизвестное действие: {action}")


async def _apply_to_seed(report_id: int, admin_id: int, action: str, seed_id: int) -> ActionResult:
    if action in {"delete", "ban", "hide24"}:
        await _deactivate_seed(seed_id)
        await db.resolve_reports_for("seed", seed_id, admin_id, "seed_off")
        await db.log_action(admin_id, "seed", seed_id, "seed_off", f"report#{report_id}")
        return ActionResult(True, f"Демо-анкета #{seed_id} снята с показа.")
    if action == "reject":
        await db.resolve_report(report_id, admin_id, "reject")
        return ActionResult(True, "Жалоба на демо-анкету отклонена.")
    return ActionResult(False, f"Неизвестное действие: {action}")


async def _deactivate_seed(seed_id: int) -> None:
    conn = await db.connect()
    await conn.execute("UPDATE rate_seed_profiles SET active=0 WHERE id=?", (seed_id,))
    await conn.commit()


# --- Форматирование карточки жалобы для админа ---------------------------

def format_report(report: dict) -> str:
    snap = report.get("snapshot") or {}
    reason = REASONS.get(report["reason"], report["reason"])
    kind_label = "демо-анкета" if snap.get("kind") == "seed" else "анкета пользователя"

    lines = [
        f"<b>Жалоба #{report['id']}</b>",
        f"Причина: {reason}",
        f"Объект: {kind_label}",
    ]
    if snap.get("name"):
        lines.append(f"Имя в анкете: {snap['name']}, {snap.get('age', '?')}")
    if snap.get("kind") == "live":
        lines.append(f"ID владельца: <code>{snap.get('id')}</code>")
        lines.append(f"Оценок на момент жалобы: {snap.get('votes', 0)}")
    lines.append(f"Отправитель: <code>{report['reporter_id']}</code>")
    if report.get("comment"):
        lines.append(f"\nКомментарий:\n<i>{_escape(report['comment'])}</i>")
    return "\n".join(lines)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
