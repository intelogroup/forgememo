"""
Server data layer — Supabase (PostgreSQL) backend.

Requires env vars:
  SUPABASE_URL              — your project URL (https://<ref>.supabase.co)
  SUPABASE_SERVICE_ROLE_KEY — service role key (bypasses RLS for server calls)
"""
from __future__ import annotations

import os
import secrets
import time

from supabase import Client, create_client

SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


class Database:
    def __init__(self, url: str = SUPABASE_URL, key: str = SUPABASE_KEY):
        self._sb: Client = create_client(url, key)

    def init(self) -> None:
        # Schema is applied via supabase_migration.sql — nothing to do at runtime.
        pass

    # ---- Users ----

    def create_user(
        self,
        email: str,
        initial_balance: float = 5.0,
        provider: str = "forgemem",
        provider_id: str | None = None,
        name: str | None = None,
        avatar_url: str | None = None,
        username: str | None = None,
    ) -> str:
        uid = secrets.token_hex(16)
        now = int(time.time())
        self._sb.table("users").insert({
            "id": uid,
            "email": email,
            "balance_usd": initial_balance,
            "created_at": now,
            "provider": provider,
            "provider_id": provider_id,
            "name": name,
            "avatar_url": avatar_url,
            "username": username,
        }).execute()
        return uid

    def get_user_by_email(self, email: str) -> dict | None:
        res = self._sb.table("users").select("*").eq("email", email).maybe_single().execute()
        return res.data

    def get_user_by_id(self, user_id: str) -> dict | None:
        res = self._sb.table("users").select("*").eq("id", user_id).maybe_single().execute()
        return res.data

    def get_user_by_provider(self, provider: str, provider_id: str) -> dict | None:
        res = (
            self._sb.table("users")
            .select("*")
            .eq("provider", provider)
            .eq("provider_id", provider_id)
            .maybe_single()
            .execute()
        )
        return res.data

    def upsert_oauth_user(
        self,
        email: str,
        provider: str,
        provider_id: str,
        name: str | None,
        avatar_url: str | None,
        username: str | None,
        initial_balance: float = 5.0,
    ) -> dict:
        user = self.get_user_by_provider(provider, provider_id)
        if user:
            self._sb.table("users").update(
                {"name": name, "avatar_url": avatar_url, "username": username}
            ).eq("id", user["id"]).execute()
            return self.get_user_by_id(user["id"])  # type: ignore[return-value]

        user = self.get_user_by_email(email)
        if user:
            self._sb.table("users").update(
                {"provider": provider, "provider_id": provider_id, "name": name,
                 "avatar_url": avatar_url, "username": username}
            ).eq("id", user["id"]).execute()
            return self.get_user_by_id(user["id"])  # type: ignore[return-value]

        uid = self.create_user(
            email,
            initial_balance=initial_balance,
            provider=provider,
            provider_id=provider_id,
            name=name,
            avatar_url=avatar_url,
            username=username,
        )
        return self.get_user_by_id(uid)  # type: ignore[return-value]

    def deduct_credits(self, user_id: str, amount: float) -> float:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")
        new_balance = round(user["balance_usd"] - amount, 6)
        if new_balance < 0:
            raise ValueError(f"Insufficient credits (balance: ${user['balance_usd']:.4f})")
        self._sb.table("users").update({"balance_usd": new_balance}).eq("id", user_id).execute()
        return new_balance

    def top_up_credits(self, user_id: str, amount: float) -> float:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found")
        new_balance = round(user["balance_usd"] + amount, 6)
        self._sb.table("users").update({"balance_usd": new_balance}).eq("id", user_id).execute()
        return new_balance

    # ---- Usage ----

    def log_run(self, user_id: str, run_id: str, cost_usd: float, model: str, balance_usd: float) -> None:
        self._sb.table("usage_runs").insert({
            "run_id": run_id,
            "user_id": user_id,
            "cost_usd": cost_usd,
            "model": model,
            "balance_usd": balance_usd,
            "ts": int(time.time()),
        }).execute()

    def run_count_in_window(self, user_id: str, window_seconds: int = 3600) -> int:
        cutoff = int(time.time()) - window_seconds
        res = (
            self._sb.table("usage_runs")
            .select("run_id", count="exact")
            .eq("user_id", user_id)
            .gt("ts", cutoff)
            .execute()
        )
        return res.count or 0

    # ---- Magic link tokens ----

    def create_magic_link_token(self, token: str, email: str, callback: str, state: str, ttl: int = 600) -> None:
        now = int(time.time())
        self._sb.table("magic_link_tokens").insert({
            "token": token,
            "email": email,
            "callback": callback,
            "state": state,
            "created_at": now,
            "expires_at": now + ttl,
            "used": False,
        }).execute()

    def consume_magic_link_token(self, token: str) -> dict | None:
        now = int(time.time())
        res = (
            self._sb.table("magic_link_tokens")
            .select("*")
            .eq("token", token)
            .eq("used", False)
            .gt("expires_at", now)
            .maybe_single()
            .execute()
        )
        row = res.data
        if row:
            self._sb.table("magic_link_tokens").update({"used": True}).eq("token", token).execute()
        return row

    # ---- Sessions ----

    def create_session(self, token: str, user_id: str, ttl_seconds: int = 30 * 86400) -> None:
        now = int(time.time())
        self._sb.table("sessions").insert({
            "token": token,
            "user_id": user_id,
            "created_at": now,
            "expires_at": now + ttl_seconds,
        }).execute()

    def get_user_by_session(self, token: str) -> dict | None:
        now = int(time.time())
        res = (
            self._sb.table("sessions")
            .select("user_id")
            .eq("token", token)
            .gt("expires_at", now)
            .maybe_single()
            .execute()
        )
        if not res.data:
            return None
        return self.get_user_by_id(res.data["user_id"])

    # ---- Stripe ----

    def stripe_event_seen(self, event_id: str) -> bool:
        res = (
            self._sb.table("stripe_events")
            .select("event_id")
            .eq("event_id", event_id)
            .maybe_single()
            .execute()
        )
        if res.data:
            return True
        self._sb.table("stripe_events").insert({
            "event_id": event_id,
            "processed_at": int(time.time()),
        }).execute()
        return False

    # ---- Sync ----

    def upsert_device(self, user_id: str, device_id: str, name: str) -> None:
        self._sb.table("devices").upsert({
            "id": device_id,
            "user_id": user_id,
            "name": name,
            "last_sync": int(time.time()),
        }, on_conflict="id").execute()

    def upsert_trace(self, user_id: str, device_id: str, t: dict) -> None:
        self._sb.table("sync_traces").upsert({
            "user_id": user_id,
            "device_id": device_id,
            "local_id": t["local_id"],
            "ts": t.get("ts"),
            "session_id": t.get("session_id"),
            "project_tag": t.get("project_tag"),
            "type": t.get("type", "note"),
            "content": t["content"],
            "distilled": bool(t.get("distilled", False)),
            "synced_at": int(time.time()),
        }, on_conflict="user_id,device_id,local_id").execute()

    def upsert_principle(self, user_id: str, device_id: str, p: dict) -> None:
        self._sb.table("sync_principles").upsert({
            "user_id": user_id,
            "device_id": device_id,
            "local_id": p["local_id"],
            "source_local_id": p.get("source_local_id"),
            "project_tag": p.get("project_tag"),
            "type": p.get("type"),
            "principle": p["principle"],
            "impact_score": int(p.get("impact_score", 5)),
            "tags": p.get("tags"),
            "synced_at": int(time.time()),
        }, on_conflict="user_id,device_id,local_id").execute()

    def pull_traces(self, user_id: str, since: int = 0, exclude_device: str = "") -> list[dict]:
        q = (
            self._sb.table("sync_traces")
            .select("*")
            .eq("user_id", user_id)
            .gt("synced_at", since)
        )
        if exclude_device:
            q = q.neq("device_id", exclude_device)
        return q.execute().data or []

    def pull_principles(self, user_id: str, since: int = 0, exclude_device: str = "") -> list[dict]:
        q = (
            self._sb.table("sync_principles")
            .select("*")
            .eq("user_id", user_id)
            .gt("synced_at", since)
        )
        if exclude_device:
            q = q.neq("device_id", exclude_device)
        return q.execute().data or []

    # ---- Stats / Activity ----

    def count_runs(self, user_id: str) -> int:
        res = (
            self._sb.table("usage_runs")
            .select("run_id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        return res.count or 0

    def get_recent_runs(self, user_id: str, limit: int = 20) -> list[dict]:
        res = (
            self._sb.table("usage_runs")
            .select("model,cost_usd,ts")
            .eq("user_id", user_id)
            .order("ts", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    def count_synced_traces(self, user_id: str) -> int:
        res = (
            self._sb.table("sync_traces")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        return res.count or 0

    def count_synced_principles(self, user_id: str) -> int:
        res = (
            self._sb.table("sync_principles")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        return res.count or 0

    def get_synced_projects(self, user_id: str) -> list[str]:
        res = (
            self._sb.table("sync_traces")
            .select("project_tag")
            .eq("user_id", user_id)
            .neq("project_tag", None)
            .neq("project_tag", "")
            .execute()
        )
        seen: set[str] = set()
        result = []
        for row in (res.data or []):
            tag = row.get("project_tag")
            if tag and tag not in seen:
                seen.add(tag)
                result.append(tag)
        return result
