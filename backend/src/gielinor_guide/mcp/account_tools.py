"""Account tools: WikiSync progress and RuneLite bank/inventory/equipment import."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from gielinor_guide.core.errors import OSRSError
from gielinor_guide.core.http import OSRSHttp
from gielinor_guide.core.runelite_export import CONTAINERS, Container, load_owned_items
from gielinor_guide.core.store import Store
from gielinor_guide.core.wikisync import DIARY_TIERS, Profile, QuestState, fetch_progress

READ_ONLY_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# import_bank writes snapshots to the local history DB, but re-running it is harmless.
LOCAL_WRITE_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

QuestStatus = Literal["completed", "in_progress", "not_started", "unknown"]

STATE_NAMES: dict[QuestState, QuestStatus] = {
    QuestState.NOT_STARTED: "not_started",
    QuestState.IN_PROGRESS: "in_progress",
    QuestState.COMPLETED: "completed",
}
MAX_CHANGES = 25


# ---------- Output schemas ----------


class QuestLookupOut(BaseModel):
    query: str
    quest: str | None = Field(description="Matched quest name, or None if no quest matched")
    state: QuestStatus


class DiaryRegionOut(BaseModel):
    region: str
    completed_tiers: list[str]
    tasks: dict[str, str] = Field(description='Per tier "done/total", e.g. {"Easy": "10/10"}')


class AccountProgressOut(BaseModel):
    rsn: str
    synced_at: str = Field(description="When WikiSync last received data (ISO 8601, UTC)")
    minutes_since_sync: int
    quests_completed: int
    quests_in_progress: list[str]
    quests_not_started: int
    quest_lookup: list[QuestLookupOut] = Field(
        description="Status of the specific quests asked about via `quests`"
    )
    completed_quest_names: list[str] | None = Field(None, description="Only with detail='full'")
    not_started_quest_names: list[str] | None = Field(None, description="Only with detail='full'")
    diaries: list[DiaryRegionOut]
    levels: dict[str, int] = Field(description="Levels as of the last sync (hiscores = live XP)")
    combat_achievements_completed: int
    collection_log_items: int
    music_tracks_unlocked: str
    notes: list[str]


class ItemOut(BaseModel):
    container: Container
    name: str
    quantity: int


class ItemChangeOut(BaseModel):
    name: str
    before: int
    after: int
    delta: int


class ContainerOut(BaseModel):
    container: Container
    exported_at: str
    minutes_old: int
    distinct_items: int
    snapshot_saved: bool = Field(description="False if this export was already imported")
    previous_exported_at: str | None
    changes_since_previous: list[ItemChangeOut] = Field(
        description=f"Biggest quantity changes vs. the previous snapshot (max {MAX_CHANGES})"
    )


class ImportBankOut(BaseModel):
    rsn: str
    export_dir: str
    containers: list[ContainerOut]
    coins: int = Field(description="Coins plus platinum tokens x1000, across all containers")
    equipped: list[str]
    search_results: list[ItemOut]
    items: list[ItemOut] | None = Field(None, description="Every item, only with list_items=true")
    notes: list[str]


# ---------- Registration ----------


def register_account_tools(
    server: MCPServer,
    *,
    client: OSRSHttp,
    resolve_rsn: Callable[[str | None], str],
    get_store: Callable[[], Store],
    now: Callable[[], datetime],
) -> None:
    @server.tool(annotations=READ_ONLY_REMOTE)
    async def get_account_progress(
        rsn: str | None = None,
        quests: list[str] | None = None,
        detail: Literal["summary", "full"] = "summary",
        profile: Profile = "STANDARD",
    ) -> AccountProgressOut:
        """Quests, Achievement Diaries, combat achievements and collection log via WikiSync.

        Use this for anything the hiscores can't show: "have I done X?", "which diaries do I
        have?", unlocks that gate training methods or quests. Requires the WikiSync RuneLite
        plugin on the account.

        Args:
            rsn: RuneScape name. Omit for the configured default account.
            quests: Specific quests to check, e.g. ["Dragon Slayer II", "legends quest"].
                Matching ignores case and punctuation.
            detail: "summary" (counts + in-progress names) or "full" (every quest name).
            profile: WikiSync profile; STANDARD for normal accounts.

        Cached for 10 minutes. `minutes_since_sync` shows how fresh the plugin's data is.
        """
        name = resolve_rsn(rsn)
        try:
            progress = await fetch_progress(client, name, profile)
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

        lookups: list[QuestLookupOut] = []
        for query in quests or []:
            hit = progress.find_quest(query)
            if hit is None:
                lookups.append(QuestLookupOut(query=query, quest=None, state="unknown"))
            else:
                quest, state = hit
                lookups.append(QuestLookupOut(query=query, quest=quest, state=STATE_NAMES[state]))

        regions: dict[str, DiaryRegionOut] = {}
        for d in progress.diaries:
            out = regions.setdefault(
                d.region, DiaryRegionOut(region=d.region, completed_tiers=[], tasks={})
            )
            out.tasks[d.tier] = f"{d.tasks_done}/{d.tasks_total}"
            if d.complete:
                out.completed_tiers.append(d.tier)
        for out in regions.values():
            out.completed_tiers.sort(key=DIARY_TIERS.index)

        age_min = max(0, int(progress.age_seconds(now()) // 60))
        notes: list[str] = []
        if age_min > 24 * 60:
            notes.append(
                f"WikiSync data is {age_min // 60}h old; log in with RuneLite to refresh it."
            )
        if not progress.collection_log_ids:
            notes.append(
                "Collection log not synced: open the collection log in game and press the "
                "WikiSync button (top right)."
            )
        if any(lk.state == "unknown" for lk in lookups):
            notes.append("Some quest names didn't match; check spelling or use detail='full'.")

        full = detail == "full"
        return AccountProgressOut(
            rsn=progress.rsn,
            synced_at=progress.synced_at.isoformat(),
            minutes_since_sync=age_min,
            quests_completed=len(progress.quests_in(QuestState.COMPLETED)),
            quests_in_progress=progress.quests_in(QuestState.IN_PROGRESS),
            quests_not_started=len(progress.quests_in(QuestState.NOT_STARTED)),
            quest_lookup=lookups,
            completed_quest_names=progress.quests_in(QuestState.COMPLETED) if full else None,
            not_started_quest_names=progress.quests_in(QuestState.NOT_STARTED) if full else None,
            diaries=list(regions.values()),
            levels=progress.levels,
            combat_achievements_completed=len(progress.combat_achievement_ids),
            collection_log_items=len(progress.collection_log_ids),
            music_tracks_unlocked=f"{progress.music_unlocked}/{progress.music_total}",
            notes=notes,
        )

    @server.tool(annotations=LOCAL_WRITE_IDEMPOTENT)
    async def import_bank(
        rsn: str | None = None,
        search: str | None = None,
        containers: list[Container] | None = None,
        list_items: bool = False,
    ) -> ImportBankOut:
        """Read bank, inventory and equipment from the RuneLite "Data Export" plugin.

        Each call saves a snapshot to local history and reports what changed since the last
        export, so it's safe (and useful) to call often. Use `search` to answer "do I have X?"
        / "how many X do I have?" instead of listing everything.

        Args:
            rsn: Account the export belongs to (for history). Omit for the default account.
            search: Case-insensitive substring, e.g. "cosmic" or "graceful".
            containers: Subset of ["bank", "inventory", "equipment"]. Default: all found.
            list_items: Return every item. Banks can be 500+ items, so prefer `search`.

        Exports only update when the plugin writes them (opening the bank, changing gear),
        so check `minutes_old` before relying on exact counts.
        """
        name = resolve_rsn(rsn)
        settings = client.settings
        try:
            owned = load_owned_items(
                settings.export_dir, rsn=name, containers=containers or CONTAINERS
            )
        except OSRSError as exc:
            raise ToolError(str(exc)) from exc

        store = get_store()
        current = now()
        container_out: list[ContainerOut] = []
        for container in CONTAINERS:
            export = owned.exports.get(container)
            if export is None:
                continue
            saved = store.save_items(name, export)
            changes = sorted(saved.changes, key=lambda c: abs(c.delta), reverse=True)
            container_out.append(
                ContainerOut(
                    container=container,
                    exported_at=export.exported_at.isoformat(),
                    minutes_old=max(0, int((current - export.exported_at).total_seconds() // 60)),
                    distinct_items=len(export.items),
                    snapshot_saved=saved.new,
                    previous_exported_at=(
                        saved.previous_exported_at.isoformat()
                        if saved.previous_exported_at
                        else None
                    ),
                    changes_since_previous=[
                        ItemChangeOut(name=c.name, before=c.before, after=c.after, delta=c.delta)
                        for c in changes[:MAX_CHANGES]
                    ],
                )
            )

        notes: list[str] = []
        missing = [c for c in (containers or CONTAINERS) if c not in owned.exports]
        if missing:
            notes.append(
                f"No export found for: {', '.join(missing)}. Enable them in the Data Export "
                "plugin settings, then open the bank / change gear so it writes the files."
            )

        equipment = owned.exports.get("equipment")
        return ImportBankOut(
            rsn=name,
            export_dir=str(settings.export_dir),
            containers=container_out,
            coins=owned.count("Coins") + 1000 * owned.count("Platinum token"),
            equipped=[i.name for i in equipment.items] if equipment else [],
            search_results=[
                ItemOut(container=c, name=i.name, quantity=i.quantity)
                for c, i in owned.search(search)
            ]
            if search
            else [],
            items=[
                ItemOut(container=c, name=i.name, quantity=i.quantity)
                for c, export in owned.exports.items()
                for i in export.items
            ]
            if list_items
            else None,
            notes=notes,
        )


def utc_now() -> datetime:
    return datetime.now(UTC)
