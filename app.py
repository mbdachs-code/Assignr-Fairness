from __future__ import annotations

import csv
import io
import json
import os
from collections import OrderedDict
from datetime import date, datetime

from flask import Flask, jsonify, make_response, render_template, request
from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, create_engine, delete, desc, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from assignr_client import AssignrClient, game_date as api_game_date

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
REPORT_FIELDNAMES = ["Name", "Bases", "Plate", "Total", "RequestedGames", "RequestedButAssignedNone"]


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


class WorkingGamePlan(Base):
    __tablename__ = "working_game_plans"

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assignments_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REPORT_FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build_bookmarklet(base_url: str) -> str:
    endpoint = json.dumps(f"{base_url.rstrip('/')}/api/import-requests")
    headers = json.dumps(REQUEST_FIELDNAMES)
    template = r'''javascript:(()=>{const delay=(ms)=>new Promise((resolve)=>setTimeout(resolve,ms));const gameIdMatch=window.location.pathname.match(/\/assign\/games\/(\d+)\//);const tabGameIdMatch=(document.querySelector('[id^="tab-requests-"]')?.id||"").match(/tab-requests-(\d+)/);const pageGameId=gameIdMatch?gameIdMatch[1]:(tabGameIdMatch?tabGameIdMatch[1]:"");const onRequestedIndex=/\/games\/requested\/?$/.test(window.location.pathname);const receiverUrl=__ENDPOINT__;const venueEl=document.querySelector("h3 + h3 a, h3 a");const venue=venueEl?venueEl.textContent.trim():"";const dateEl=document.querySelector("h3");const gameDateText=dateEl?dateEl.textContent.trim():"";const collectRows=()=>{const rows=[];const seen=new Set();document.querySelectorAll('[id^="tab-requests-"] p').forEach((node)=>{const deleteLink=node.querySelector('a[href*="/game_requests/"]');if(!deleteLink)return;const href=deleteLink.getAttribute("href")||"";const rowGameMatch=href.match(/\/games\/(\d+)\/game_requests\/(\d+)\//);const requestMatch=href.match(/\/game_requests\/(\d+)\//);if(!requestMatch)return;const requestId=requestMatch[1];const rowGameId=rowGameMatch?rowGameMatch[1]:pageGameId;if(seen.has(requestId))return;seen.add(requestId);const strong=node.querySelector("strong");const name=strong?strong.textContent.replace(/,\s*$/,"").trim():"";const timestampNode=node.querySelector("span[title]");const requestTimestamp=timestampNode?timestampNode.getAttribute("title").trim():"";let requestedPosition=node.textContent||"";requestedPosition=requestedPosition.replace(name,"");requestedPosition=requestedPosition.replace(/\[[^\]]*Delete[^\]]*\]/gi,"");requestedPosition=requestedPosition.replace(/\([^)]*\)/g,"");requestedPosition=requestedPosition.replace(/\s+/g," ").replace(/^,\s*|\s*,$/g,"").trim();rows.push({game_id:rowGameId,game_date:gameDateText,venue:venue,home_team:"",away_team:"",request_id:requestId,requester:name,requested_position:requestedPosition,request_timestamp:requestTimestamp,declined:node.classList.contains("declined")?"yes":"no"});});return rows;};const openRequestTabs=async()=>{const toggles=Array.from(document.querySelectorAll(['a[href^="#tab-requests-"]','[data-bs-target^="#tab-requests-"]','[data-target^="#tab-requests-"]','[aria-controls^="tab-requests-"]'].join(",")));for(const toggle of toggles){toggle.dispatchEvent(new MouseEvent("click",{bubbles:true,cancelable:true}));await delay(120);}if(toggles.length){await delay(400);}};const headers=__HEADERS__;const finalize=(rows)=>{const csv=[headers.join(","),...rows.map((row)=>headers.map((key)=>`"${String(row[key]||"").replace(/"/g,'""')}"`).join(","))].join("\n");const missingGameIds=rows.filter((row)=>!String(row.game_id||"").trim()).length;const sendImport=()=>fetch(receiverUrl,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({rows:rows,replace:true})}).then(async(response)=>{const payload=await response.json().catch(()=>({}));if(!response.ok){throw new Error(payload.error||`Import failed with status ${response.status}`);}return payload;});const tryClipboard=()=>{if(!(navigator.clipboard&&window.isSecureContext)){return Promise.resolve(false);}return navigator.clipboard.writeText(csv).then(()=>true).catch(()=>false);};if(!rows.length){alert("No request rows were found on this page. Make sure the Requests tab is visible.");}else if(missingGameIds){alert(onRequestedIndex?`Copied ${rows.length} request row(s), but game_id is missing on ${missingGameIds} row(s). Open an individual Assign page or the main games page for per-game matching.`:`Copied ${rows.length} request row(s), but game_id is missing on ${missingGameIds} row(s).`);}else{sendImport().then((payload)=>tryClipboard().then((copied)=>({payload,copied}))).then(({payload,copied})=>{alert(copied?`Imported ${payload.imported_rows} request row(s). Final report now has ${payload.final_rows} row(s).`:`Imported ${payload.imported_rows} request row(s). Final report now has ${payload.final_rows} row(s). Clipboard copy was skipped.`);}).catch((err)=>alert(`Assignr import failed: ${err.message}`));}};(async()=>{let rows=collectRows();if(!rows.length){await openRequestTabs();rows=collectRows();}finalize(rows);})();})();'''
    return template.replace("__ENDPOINT__", endpoint).replace("__HEADERS__", headers)


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

    def parse_optional_date(value: str | None) -> date | None:
        value = (value or "").strip()
        if not value:
            return None
        return parse_date(value)

    def normalize_assignment_payload(assignments: list[dict[str, str]] | None) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for assignment in assignments or []:
            if not isinstance(assignment, dict):
                continue
            name = canonical_name(str(assignment.get("name", "")))
            position = normalize_spaces(str(assignment.get("position", "")))
            if not name or not position:
                continue
            normalized.append({"name": name, "position": position})
        return normalized

    def load_working_plans(session: Session) -> dict[str, dict[str, object]]:
        plans: dict[str, dict[str, object]] = {}
        for plan in session.scalars(select(WorkingGamePlan)).all():
            plans[normalize_spaces(plan.game_id)] = {
                "assignments": normalize_assignment_payload(json.loads(plan.assignments_json or "[]")),
                "updated_at": plan.updated_at,
            }
        return plans

    def filtered_snapshot_games(
        session: Session,
        review_start: date | None = None,
        review_end: date | None = None,
    ) -> list[SnapshotGame]:
        run = latest_run(session)
        if not run:
            return []
        games = list(session.scalars(select(SnapshotGame).where(SnapshotGame.run_id == run.id)).all())
        kept: list[SnapshotGame] = []
        for game in games:
            game_day = parse_optional_date(normalize_spaces(game.game_date))
            if review_start and game_day and game_day < review_start:
                continue
            if review_end and game_day and game_day > review_end:
                continue
            kept.append(game)
        kept.sort(key=lambda game: (normalize_spaces(game.game_date), normalize_spaces(game.home_team), normalize_spaces(game.away_team), normalize_spaces(game.game_id)))
        return kept

    def request_counts_for_games(
        session: Session,
        included_game_ids: set[str],
        include_declined: bool = False,
    ) -> OrderedDict[str, set[str]]:
        counts: OrderedDict[str, set[str]] = OrderedDict()
        for row in session.scalars(select(RequestRow).order_by(RequestRow.id)).all():
            if row.declined and not include_declined:
                continue
            game_id = normalize_spaces(row.game_id)
            if included_game_ids and game_id not in included_game_ids:
                continue
            name = canonical_name(row.requester)
            if not name or not game_id:
                continue
            counts.setdefault(name, set()).add(game_id)
        return counts

    def build_report_rows(
        session: Session,
        review_start: date | None = None,
        review_end: date | None = None,
    ) -> list[dict[str, str]]:
        working_plans = load_working_plans(session)
        games = filtered_snapshot_games(session, review_start, review_end)
        counts: dict[str, dict[str, int]] = {}
        included_game_ids = {normalize_spaces(game.game_id) for game in games if normalize_spaces(game.game_id)}

        for game in games:
            game_id = normalize_spaces(game.game_id)
            working = working_plans.get(game_id, {})
            assignments = working.get("assignments") or normalize_assignment_payload(json.loads(game.assignments_json or "[]"))
            for assignment in assignments:
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

        requested_games = request_counts_for_games(session, included_game_ids)
        rows: list[dict[str, str]] = []
        seen_names: set[str] = set()

        for canon in sorted(counts):
            seen_names.add(canon)
            requested = len(requested_games.get(canon, set()))
            total_assigned = counts[canon]["Total"]
            rows.append(
                {
                    "Name": to_last_first(canon),
                    "Bases": str(counts[canon]["Bases"]),
                    "Plate": str(counts[canon]["Plate"]),
                    "Total": str(total_assigned),
                    "RequestedGames": str(requested),
                    "RequestedButAssignedNone": "YES" if requested > 0 and total_assigned == 0 else "",
                }
            )

        for canon in sorted(name for name in requested_games if name not in seen_names):
            rows.append(
                {
                    "Name": to_last_first(canon),
                    "Bases": "0",
                    "Plate": "0",
                    "Total": "0",
                    "RequestedGames": str(len(requested_games[canon])),
                    "RequestedButAssignedNone": "YES",
                }
            )
        return rows

    def build_game_rows(
        session: Session,
        review_start: date | None = None,
        review_end: date | None = None,
    ) -> list[dict[str, object]]:
        working_plans = load_working_plans(session)
        games = filtered_snapshot_games(session, review_start, review_end)
        rows: list[dict[str, object]] = []
        for game in games:
            game_id = normalize_spaces(game.game_id)
            official_assignments = normalize_assignment_payload(json.loads(game.assignments_json or "[]"))
            working = working_plans.get(game_id)
            working_assignments = list(working.get("assignments", [])) if working else []
            effective_assignments = working_assignments or official_assignments
            official_by_position = {row["position"]: row["name"] for row in official_assignments}
            effective_by_position = {row["position"]: row["name"] for row in effective_assignments}
            change_summary: list[str] = []
            for position in sorted(set(official_by_position) | set(effective_by_position)):
                official_name = official_by_position.get(position, "Unassigned")
                effective_name = effective_by_position.get(position, "Unassigned")
                if official_name != effective_name:
                    change_summary.append(f"{position}: {official_name} -> {effective_name}")
            rows.append(
                {
                    "game_id": game_id,
                    "game_date": normalize_spaces(game.game_date),
                    "home_team": normalize_spaces(game.home_team),
                    "away_team": normalize_spaces(game.away_team),
                    "official_assignments": official_assignments,
                    "working_assignments": working_assignments,
                    "effective_assignments": effective_assignments,
                    "has_working_changes": bool(working_assignments),
                    "change_summary": change_summary,
                    "change_count": len(change_summary),
                    "updated_at": working["updated_at"].isoformat() if working and working.get("updated_at") else "",
                }
            )
        return rows

    def candidate_names(session: Session) -> list[str]:
        names: set[str] = set()
        for row in session.scalars(select(RequestRow.requester)).all():
            canon = canonical_name(row)
            if canon:
                names.add(canon)
        for game in session.scalars(select(SnapshotGame)).all():
            for assignment in normalize_assignment_payload(json.loads(game.assignments_json or "[]")):
                canon = canonical_name(assignment["name"])
                if canon:
                    names.add(canon)
        for plan in session.scalars(select(WorkingGamePlan)).all():
            for assignment in normalize_assignment_payload(json.loads(plan.assignments_json or "[]")):
                canon = canonical_name(assignment["name"])
                if canon:
                    names.add(canon)
        return sorted(names, key=lambda name: name.lower())

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        return response

    @app.context_processor
    def inject_globals() -> dict[str, str]:
        base_url = os.getenv("PUBLIC_APP_URL", "").strip() or request.url_root.rstrip("/")
        return {"bookmarklet_code": build_bookmarklet(base_url)}

    @app.get("/")
    def index():
        with Session(engine) as session:
            run = latest_run(session)
            rows = build_report_rows(session)
            request_total = len(session.scalars(select(RequestRow.id)).all())
            game_rows = build_game_rows(session)
            candidates = candidate_names(session)
        requested_none_count = sum(1 for row in rows if row.get("RequestedButAssignedNone") == "YES")
        working_change_count = sum(1 for game in game_rows if game.get("has_working_changes"))
        return render_template(
            "index.html",
            rows=rows,
            games=game_rows,
            candidates=candidates,
            row_count=len(rows),
            request_total=request_total,
            requested_none_count=requested_none_count,
            working_change_count=working_change_count,
            run=run,
        )

    @app.get("/api/bookmarklet")
    def bookmarklet():
        base_url = os.getenv("PUBLIC_APP_URL", "").strip() or request.url_root.rstrip("/")
        return jsonify({"bookmarklet": build_bookmarklet(base_url)})

    @app.route("/api/import-requests", methods=["OPTIONS"])
    def import_requests_options():
        return make_response(("", 204))

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
            dedupe_key = request_id or "|".join(
                [
                    normalize_spaces(str(raw.get("game_id", ""))),
                    normalize_spaces(str(raw.get("requester", ""))),
                    normalize_spaces(str(raw.get("requested_position", ""))),
                ]
            )
            if dedupe_key in seen_request_ids:
                continue
            seen_request_ids.add(dedupe_key)
            normalized.append(
                RequestRow(
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
                )
            )

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
            final_rows = build_report_rows(session)
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
                game_day = api_game_date(game)
                session.add(
                    SnapshotGame(
                        run_id=run.id,
                        game_id=str(game.get("id", "") or ""),
                        game_date=game_day.isoformat() if game_day else str(game.get("game_date") or game.get("date") or ""),
                        home_team=str(game.get("home_team", "") or ""),
                        away_team=str(game.get("away_team", "") or ""),
                        assignments_json=json.dumps(client.assignment_summary(game)),
                    )
                )
            session.commit()
            final_rows = build_report_rows(session)
        return jsonify({"ok": True, "games_fetched": len(games), "final_rows": len(final_rows)})

    @app.get("/api/final-report")
    def final_report():
        review_start = parse_optional_date(request.args.get("review_start"))
        review_end = parse_optional_date(request.args.get("review_end"))
        with Session(engine) as session:
            rows = build_report_rows(session, review_start, review_end)
        return jsonify({"rows": rows, "csv": csv_text(rows)})

    @app.get("/api/games")
    def games():
        review_start = parse_optional_date(request.args.get("review_start"))
        review_end = parse_optional_date(request.args.get("review_end"))
        search = normalize_spaces(request.args.get("search", "")).lower()
        with Session(engine) as session:
            rows = build_game_rows(session, review_start, review_end)
            candidates = candidate_names(session)
        if search:
            rows = [
                game
                for game in rows
                if search in f"{game['home_team']} {game['away_team']} {game['game_id']}".lower()
            ]
        return jsonify({"games": rows, "candidates": candidates})

    @app.route("/api/working-plan", methods=["OPTIONS"])
    def working_plan_options():
        return make_response(("", 204))

    @app.post("/api/working-plan")
    def save_working_plan():
        payload = request.get_json(silent=True) or {}
        game_id = normalize_spaces(str(payload.get("game_id", "")))
        assignments = normalize_assignment_payload(payload.get("assignments") if isinstance(payload.get("assignments"), list) else [])
        if not game_id:
            return jsonify({"error": "game_id is required."}), 400
        with Session(engine) as session:
            existing = session.get(WorkingGamePlan, game_id)
            if existing is None:
                existing = WorkingGamePlan(game_id=game_id)
                session.add(existing)
            existing.assignments_json = json.dumps(assignments)
            existing.updated_at = datetime.utcnow()
            session.commit()
        return jsonify({"ok": True, "saved": len(assignments)})

    @app.delete("/api/working-plan/<game_id>")
    def clear_working_plan(game_id: str):
        game_id = normalize_spaces(game_id)
        with Session(engine) as session:
            existing = session.get(WorkingGamePlan, game_id)
            if existing is not None:
                session.delete(existing)
                session.commit()
        return jsonify({"ok": True})

    @app.delete("/api/working-plan")
    def clear_all_working_plans():
        with Session(engine) as session:
            session.execute(delete(WorkingGamePlan))
            session.commit()
        return jsonify({"ok": True})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
