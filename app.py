from __future__ import annotations

import csv
import io
import json
import os
from collections import OrderedDict
from datetime import date, datetime

from flask import Flask, jsonify, render_template, request
from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, create_engine, delete, desc, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from assignr_client import AssignrClient

REQUEST_FIELDNAMES = [
    "game_id",
    "game_date",
    "venue",
    "home_team",
    "away_team",
    "request_id",
    "requester",
    "requested_position",
    "request_timestamp",
    "declined",
]


class Base(DeclarativeBase):
    pass


class RequestRow(Base):
    __tablename__ = "request_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    game_date: Mapped[str] = mapped_column(String(64), default="")
    venue: Mapped[str] = mapped_column(String(255), default="")
    home_team: Mapped[str] = mapped_column(String(255), default="")
    away_team: Mapped[str] = mapped_column(String(255), default="")
    request_id: Mapped[str] = mapped_column(String(128), index=True, default="")
    requester: Mapped[str] = mapped_column(String(255), index=True, default="")
    requested_position: Mapped[str] = mapped_column(String(255), default="")
    request_timestamp: Mapped[str] = mapped_column(String(255), default="")
    declined: Mapped[bool] = mapped_column(Boolean, default=False)


class SnapshotRun(Base):
    __tablename__ = "snapshot_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)


class SnapshotGame(Base):
    __tablename__ = "snapshot_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    game_date: Mapped[str] = mapped_column(String(64), default="")
    home_team: Mapped[str] = mapped_column(String(255), default="")
    away_team: Mapped[str] = mapped_column(String(255), default="")
    assignments_json: Mapped[str] = mapped_column(Text, default="[]")


def normalize_spaces(value: str) -> str:
    return " ".join((value or "").strip().split())


def canonical_name(value: str) -> str:
    raw = normalize_spaces(value)
    if not raw:
        return ""
    if "," in raw:
        last, first = [normalize_spaces(part) for part in raw.split(",", 1)]
        return normalize_spaces(f"{first} {last}")
    return raw


def to_last_first(value: str) -> str:
    raw = canonical_name(value)
    if not raw:
        return ""
    parts = raw.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def csv_text(rows: list[dict[str, str]]) -> str:
    fieldnames = ["Name", "Bases", "Plate", "Total", "RequestedGames", "RequestedButAssignedNone"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build_bookmarklet(base_url: str) -> str:
    endpoint = f"{base_url.rstrip('/')}/api/import-requests"
    js = f"""
javascript:(async()=>{{const endpoint={json.dumps(endpoint)};const F={json.dumps(REQUEST_FIELDNAMES)};const norm=v=>String(v??'').replace(/\\s+/g,' ').trim();const txt=n=>norm((n&&n.textContent)||'');const esc=v=>{{const s=String(v??'');return /[",\\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}};const getGameId=node=>{{const c=node.closest('[id^="tab-requests-"], [data-game-id], tr, .game-row, .panel, .card');if(c?.dataset?.gameId)return norm(c.dataset.gameId);if(c?.id?.startsWith('tab-requests-'))return norm(c.id.replace('tab-requests-',''));const link=c?.querySelector('a[href*="/games/"]');const match=link?.href?.match(/\\/games\\/(\\d+)/);return match?match[1]:'';}};const meta=node=>{{const c=node.closest('[id^="tab-requests-"], [data-game-id], tr, .game-row, .panel, .card')||node;const t=Array.from(c.querySelectorAll('td, .team, .teams span')).map(txt).filter(Boolean);return{{game_id:getGameId(node),game_date:txt(c.querySelector('.game-date, [data-date], .date'))||'',venue:txt(c.querySelector('.venue, [data-venue]'))||'',home_team:t[0]||'',away_team:t[1]||''}};}};const rows=[];for(const root of Array.from(document.querySelectorAll('[id^="tab-requests-"], .requests, .tab-pane, .panel, table'))){{for(const tr of root.querySelectorAll('tr')){{const cells=Array.from(tr.querySelectorAll('td'));if(cells.length<2)continue;const requester=txt(cells[0]);if(!requester||/requester/i.test(requester))continue;const requestLink=tr.querySelector('a[href*="requests"]');const requestId=requestLink?.href?.match(/requests\\/(\\d+)/)?.[1]||'';const position=cells.map(txt).find(v=>/plate|base|any position/i.test(v))||'';rows.push({{...meta(tr),request_id:requestId,requester,requested_position:position,request_timestamp:'',declined:/declined/i.test(txt(tr))?'yes':'no'}});}}}}const cleaned=[];const seen=new Set();for(const row of rows){{if(!row.game_id||!row.requester)continue;const key=`${{row.request_id}}|${{row.game_id}}|${{row.requester}}|${{row.requested_position}}`;if(seen.has(key))continue;seen.add(key);cleaned.push(row);}}if(!cleaned.length){{alert('No request rows were found on this page. Make sure the Requests tab is visible.');return;}}const csv=[F.join(',')].concat(cleaned.map(r=>F.map(k=>esc(r[k]||'')).join(','))).join('\\n');await navigator.clipboard.writeText(csv);const response=await fetch(endpoint,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{rows:cleaned,replace:true}})}});const result=await response.json();if(!response.ok)throw new Error(result.error||'Import failed');alert(`Imported ${{result.imported_rows}} request row(s). Final report now has ${{result.final_rows}} row(s).`);}})().catch(err=>alert(`Assignr import failed: ${{err.message}}`));
""".strip()
    return js


def create_app() -> Flask:
    app = Flask(__name__)
    database_url = os.getenv("DATABASE_URL", "sqlite:///assignr_hosted.db")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)

    def latest_run(session: Session) -> SnapshotRun | None:
        return session.scalar(select(SnapshotRun).order_by(desc(SnapshotRun.created_at)).limit(1))

    def assignment_rows_for_run(session: Session, run_id: int) -> list[dict[str, str]]:
        counts: dict[str, dict[str, int]] = {}
        for game in session.scalars(select(SnapshotGame).where(SnapshotGame.run_id == run_id)).all():
            for assignment in json.loads(game.assignments_json or "[]"):
                name = canonical_name(str(assignment.get("name", "")))
                if not name:
                    continue
                position = normalize_spaces(str(assignment.get("position", ""))).lower()
                person = counts.setdefault(name, {"Bases": 0, "Plate": 0, "Total": 0})
                if "base" in position:
                    person["Bases"] += 1
                elif "plate" in position:
                    person["Plate"] += 1
                person["Total"] += 1
        rows = []
        for canon in sorted(counts):
            rows.append({
                "Name": to_last_first(canon),
                "Bases": str(counts[canon]["Bases"]),
                "Plate": str(counts[canon]["Plate"]),
                "Total": str(counts[canon]["Total"]),
            })
        return rows

    def request_counts(session: Session, include_declined: bool = False) -> OrderedDict[str, set[str]]:
        counts: OrderedDict[str, set[str]] = OrderedDict()
        for row in session.scalars(select(RequestRow).order_by(RequestRow.id)).all():
            if row.declined and not include_declined:
                continue
            name = canonical_name(row.requester)
            game_id = normalize_spaces(row.game_id)
            if not name or not game_id:
                continue
            counts.setdefault(name, set()).add(game_id)
        return counts

    def merged_report_rows(session: Session) -> list[dict[str, str]]:
        run = latest_run(session)
        report_rows = assignment_rows_for_run(session, run.id) if run else []
        requested_games = request_counts(session)
        merged: list[dict[str, str]] = []
        seen_names: set[str] = set()
        for row in report_rows:
            report_name = normalize_spaces(row.get("Name", ""))
            canon = canonical_name(report_name)
            seen_names.add(canon)
            requested = len(requested_games.get(canon, set()))
            total_assigned = int(row.get("Total", "0") or "0")
            merged.append({
                "Name": report_name,
                "Bases": row.get("Bases", "0") or "0",
                "Plate": row.get("Plate", "0") or "0",
                "Total": row.get("Total", "0") or "0",
                "RequestedGames": str(requested),
                "RequestedButAssignedNone": "YES" if requested > 0 and total_assigned == 0 else "",
            })
        for canon in sorted(name for name in requested_games if name not in seen_names):
            merged.append({
                "Name": to_last_first(canon),
                "Bases": "0",
                "Plate": "0",
                "Total": "0",
                "RequestedGames": str(len(requested_games[canon])),
                "RequestedButAssignedNone": "YES",
            })
        return merged

    @app.context_processor
    def inject_globals() -> dict[str, str]:
        base_url = os.getenv("PUBLIC_APP_URL", "").strip() or request.url_root.rstrip("/")
        return {"bookmarklet_code": build_bookmarklet(base_url)}

    @app.get("/")
    def index():
        with Session(engine) as session:
            run = latest_run(session)
            rows = merged_report_rows(session)
            request_total = len(session.scalars(select(RequestRow.id)).all())
        return render_template("index.html", rows=rows[:250], row_count=len(rows), request_total=request_total, run=run)

    @app.get("/api/bookmarklet")
    def bookmarklet():
        base_url = os.getenv("PUBLIC_APP_URL", "").strip() or request.url_root.rstrip("/")
        return jsonify({"bookmarklet": build_bookmarklet(base_url)})

    @app.post("/api/import-requests")
    def import_requests():
        payload = request.get_json(silent=True) or {}
        rows = payload.get("rows")
        replace = bool(payload.get("replace", False))
        if not isinstance(rows, list) or not rows:
            return jsonify({"error": "Expected a non-empty rows array."}), 400

        start = parse_date(payload["period_start"]) if isinstance(payload.get("period_start"), str) and payload.get("period_start") else None
        end = parse_date(payload["period_end"]) if isinstance(payload.get("period_end"), str) and payload.get("period_end") else None

        normalized: list[RequestRow] = []
        seen_request_ids: set[str] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                return jsonify({"error": "Each row must be an object."}), 400
            request_id = normalize_spaces(str(raw.get("request_id", "")))
            dedupe_key = request_id or "|".join([
                normalize_spaces(str(raw.get("game_id", ""))),
                normalize_spaces(str(raw.get("requester", ""))),
                normalize_spaces(str(raw.get("requested_position", ""))),
            ])
            if dedupe_key in seen_request_ids:
                continue
            seen_request_ids.add(dedupe_key)
            normalized.append(RequestRow(
                period_start=start,
                period_end=end,
                game_id=normalize_spaces(str(raw.get("game_id", ""))),
                game_date=normalize_spaces(str(raw.get("game_date", ""))),
                venue=normalize_spaces(str(raw.get("venue", ""))),
                home_team=normalize_spaces(str(raw.get("home_team", ""))),
                away_team=normalize_spaces(str(raw.get("away_team", ""))),
                request_id=request_id,
                requester=normalize_spaces(str(raw.get("requester", ""))),
                requested_position=normalize_spaces(str(raw.get("requested_position", ""))),
                request_timestamp=normalize_spaces(str(raw.get("request_timestamp", ""))),
                declined=normalize_spaces(str(raw.get("declined", ""))).lower() == "yes",
            ))

        with Session(engine) as session:
            if replace:
                session.execute(delete(RequestRow))
            existing_ids = {rid for rid in session.scalars(select(RequestRow.request_id)).all() if rid}
            imported = 0
            for row in normalized:
                if row.request_id and row.request_id in existing_ids:
                    continue
                session.add(row)
                if row.request_id:
                    existing_ids.add(row.request_id)
                imported += 1
            session.commit()
            final_rows = merged_report_rows(session)
        return jsonify({"ok": True, "imported_rows": imported, "final_rows": len(final_rows)})

    @app.post("/api/refresh")
    def refresh():
        payload = request.get_json(silent=True) or {}
        start_raw = payload.get("start_date")
        end_raw = payload.get("end_date")
        if not isinstance(start_raw, str) or not isinstance(end_raw, str):
            return jsonify({"error": "Provide start_date and end_date in YYYY-MM-DD format."}), 400
        start_date = parse_date(start_raw)
        end_date = parse_date(end_raw)
        if end_date < start_date:
            return jsonify({"error": "end_date must be on or after start_date."}), 400
        try:
            client = AssignrClient.from_env()
            games = client.fetch_games(start_date, end_date)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        with Session(engine) as session:
            run = SnapshotRun(period_start=start_date, period_end=end_date)
            session.add(run)
            session.flush()
            for game in games:
                session.add(SnapshotGame(
                    run_id=run.id,
                    game_id=str(game.get("id", "") or ""),
                    game_date=str(game.get("game_date") or game.get("date") or ""),
                    home_team=str(game.get("home_team", "") or ""),
                    away_team=str(game.get("away_team", "") or ""),
                    assignments_json=json.dumps(client.assignment_summary(game)),
                ))
            session.commit()
            final_rows = merged_report_rows(session)
        return jsonify({"ok": True, "games_fetched": len(games), "final_rows": len(final_rows)})

    @app.get("/api/final-report")
    def final_report():
        with Session(engine) as session:
            rows = merged_report_rows(session)
        return jsonify({"rows": rows, "csv": csv_text(rows)})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
