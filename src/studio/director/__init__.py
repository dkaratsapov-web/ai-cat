"""Режим «контент-директор»: ChatGPT утверждает бриф и ревьюит, Claude Code исполняет.

brief.py   — Director Brief (YAML) → сценарий студии; locked-поля нельзя менять без нового брифа
state.py   — статусы сцен draft → approved → generating → generated → review → revise → final
qc.py      — QC идентичности кота (контактный лист + чек-лист; вердикт ставит человек), превью-кадры
review.py  — review-пакет для директора: контактный лист, кадры, превью, таблица сцен, расходы
assets.py  — библиотека approved_assets (утверждённые кадры кота, логотипы, макеты, музыка)
"""
