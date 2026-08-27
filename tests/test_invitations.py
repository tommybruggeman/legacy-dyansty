from __future__ import annotations

import hashlib
import importlib
import io
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260716_owner_invitation_stage2.sql"


auth_stub = types.ModuleType("auth")
auth_stub.auth_client = lambda: None
auth_stub.current_user = lambda: {"id": "commissioner-user", "email": "commish@example.com"}
auth_stub.service_client = lambda: None
existing_auth = sys.modules.setdefault("auth", auth_stub)
existing_auth.auth_client = auth_stub.auth_client
existing_auth.current_user = auth_stub.current_user
existing_auth.service_client = auth_stub.service_client
sys.path.insert(0, str(ROOT))

invitations = importlib.import_module("services.invitations")


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table, action, payload=None):
        self.client = client
        self.table = table
        self.action = action
        self.payload = payload
        self.filters = []
        self.select_cols = None

    def select(self, cols):
        self.select_cols = cols
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def lt(self, key, value):
        self.filters.append(("lt", key, value))
        return self

    def limit(self, value):
        self.filters.append(("limit", value))
        return self

    def order(self, *args, **kwargs):
        self.filters.append(("order", args, kwargs))
        return self

    def execute(self):
        self.client.queries.append(self)

        if self.table == "league_memberships":
            return Result([{"id": "membership-1", "role": "commissioner"}] if self.client.is_commissioner else [])

        if self.table == "league_teams":
            return Result([{
                "id": self.client.team_id,
                "league_id": self.client.league_id,
                "team_name": self.client.team_name,
                "owner_name": self.client.owner_name,
            }] if self.client.team_valid else [])

        if self.table == "leagues":
            return Result([{"name": self.client.league_name}])

        if self.table == "league_invites" and self.action == "select":
            if self.client.select_invitation_rows is not None:
                return Result(self.client.select_invitation_rows)
            return Result(self.client.pending_rows)

        if self.table == "league_invites" and self.action == "insert":
            row = {"id": "invite-created", **self.payload}
            self.client.inserted.append(self.payload)
            return Result([row])

        if self.table == "league_invites" and self.action == "update":
            self.client.updated.append((self.payload, list(self.filters)))
            if self.client.update_conflict:
                return Result([])
            row = {"id": "invite-updated", **self.client.update_base, **self.payload}
            return Result([row])

        return Result([])


class FakeTable:
    def __init__(self, client, table):
        self.client = client
        self.table = table

    def select(self, cols):
        return FakeQuery(self.client, self.table, "select").select(cols)

    def insert(self, payload):
        return FakeQuery(self.client, self.table, "insert", payload)

    def update(self, payload):
        return FakeQuery(self.client, self.table, "update", payload)


class FakeRpc:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.rpc_calls.append((self.name, self.params))
        data = self.client.rpc_data
        if data is None:
            data = [
                {
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "membership_id": "membership-accepted",
                    "role": "member",
                    "email": "owner@example.com",
                }
            ]
        return Result(data)


class FailingRpc:
    def __init__(self, error):
        self.error = error

    def execute(self):
        if isinstance(self.error, BaseException):
            raise self.error
        raise Exception(self.error)


class FakeApiError(Exception):
    def __init__(self, *, code=None, message=None, details=None, hint=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.hint = hint


class FakeClient:
    def __init__(self):
        self.is_commissioner = True
        self.team_valid = True
        self.league_id = "league-1"
        self.team_id = "team-1"
        self.league_name = "ABs Always Open"
        self.team_name = "The Long Game"
        self.owner_name = "Owner Name"
        self.pending_rows = []
        self.select_invitation_rows = None
        self.update_base = {
            "id": "invite-1",
            "email": "owner@example.com",
            "league_id": "league-1",
            "league_team_id": "team-1",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "status": "pending",
            "token_hash": hashlib.sha256(b"old-token").hexdigest(),
            "send_count": 5,
            "last_sent_at": None,
        }
        self.update_conflict = False
        self.inserted = []
        self.updated = []
        self.queries = []
        self.rpc_calls = []
        self.rpc_error = None
        self.rpc_data = None

    def table(self, table):
        return FakeTable(self, table)

    def rpc(self, name, params):
        if self.rpc_error:
            self.rpc_calls.append((name, params))
            return FailingRpc(self.rpc_error)
        return FakeRpc(self, name, params)


class InvitationHelpersTest(unittest.TestCase):
    def test_email_and_token_helpers(self):
        self.assertEqual(invitations.normalize_email("  OWNER@Example.COM "), "owner@example.com")
        self.assertTrue(invitations.validate_email("owner@example.com"))
        self.assertFalse(invitations.validate_email("owner-at-example"))

        token_a = invitations.generate_invite_token()
        token_b = invitations.generate_invite_token()
        self.assertGreaterEqual(len(token_a), 32)
        self.assertNotEqual(token_a, token_b)

        expected = hashlib.sha256(b"raw-token").hexdigest()
        self.assertEqual(invitations.hash_invite_token("raw-token"), expected)
        self.assertEqual(invitations.hash_invite_token("raw-token"), expected)

        url = invitations.build_invite_url("raw-token", "https://app.example/invite")
        self.assertIn("raw-token", url)
        self.assertNotIn(expected, url)


class InvitationEmailTemplateTest(unittest.TestCase):
    def build_email(self, **overrides):
        values = {
            "league_name": "ABs Always Open",
            "team_name": "The Long Game",
            "role_label": "Owner",
            "invited_email": "owner@example.com",
            "invite_url": "https://legacy.example/invite?invite_token=copyable-token",
            "expires_at": datetime(2027, 1, 2, 12, 0, tzinfo=timezone.utc),
            "logo_url": "https://cdn.example.com/legacy-logo.png",
            "commissioner_name": "Tommy Commissioner",
        }
        values.update(overrides)
        return invitations.build_league_invite_email(**values)

    def test_approved_palette_heading_cta_and_dynamic_values_render(self):
        email = self.build_email()
        html = email["html"]

        for color in ["#03140D", "#081F15", "#0A271B", "#C89B4A", "#F5EBD7", "#CFC6B4"]:
            self.assertIn(color, html)

        self.assertEqual(email["subject"], "You’ve been invited to join ABs Always Open")
        self.assertIn("Claim your team and enter the league.", html)
        self.assertIn("Your team is waiting.", html)
        self.assertIn("Accept Invitation", html)
        self.assertIn("ABs Always Open", html)
        self.assertIn("The Long Game", html)
        self.assertIn("owner@example.com", html)
        self.assertIn("Owner", html)
        self.assertIn("January 2, 2027", html)
        self.assertIn("Tommy Commissioner", html)
        self.assertIn('src="https://cdn.example.com/legacy-logo.png"', html)
        self.assertIn("Please do not forward this email. The invitation link is tied to the invited email address.", html)

    def test_missing_expiration_and_commissioner_degrade_gracefully(self):
        email = self.build_email(expires_at=None, commissioner_name=None, logo_url="")

        self.assertIn("This invitation will expire soon.", email["html"])
        self.assertIn("This invitation was sent by your commissioner through Legacy Dynasty.", email["html"])
        self.assertNotIn("<img src=", email["html"])

    def test_html_and_attribute_values_are_escaped(self):
        email = self.build_email(
            league_name="<League & Co>",
            team_name='Team "Dynasty"',
            role_label="<Co-Owner>",
            invited_email="owner+test@example.com",
            invite_url='https://legacy.example/invite?invite_token=abc123&next="team"',
            logo_url='https://cdn.example.com/logo.png?x=1&name="legacy"',
            commissioner_name="<Commissioner>",
        )
        html = email["html"]

        self.assertIn("&lt;League &amp; Co&gt;", html)
        self.assertIn('Team "Dynasty"', html)
        self.assertIn("&lt;Co-Owner&gt;", html)
        self.assertIn("owner+test@example.com", html)
        self.assertIn("invite_token=abc123&amp;next=&quot;team&quot;", html)
        self.assertIn("logo.png?x=1&amp;name=&quot;legacy&quot;", html)
        self.assertIn("&lt;Commissioner&gt;", html)
        self.assertNotIn("<League & Co>", html)
        self.assertNotIn("<Commissioner>", html)

    def test_plain_text_fallback_contains_core_invitation_content(self):
        email = self.build_email(role_label="Co-Owner")
        text = email["text"]

        self.assertIn("Legacy Dynasty", text)
        self.assertIn("Claim your team and enter the league.", text)
        self.assertIn("ABs Always Open", text)
        self.assertIn("The Long Game", text)
        self.assertIn("Role: Co-Owner", text)
        self.assertIn("Invited email: owner@example.com", text)
        self.assertIn("Accept Invitation:", text)
        self.assertIn("https://legacy.example/invite?invite_token=copyable-token", text)

    def test_create_and_resend_use_same_template_helper_without_logging_token(self):
        original_prepare = invitations._prepare_invitation_email
        original_deliver = invitations._deliver_invitation_email
        prepare_calls = []
        delivered_payloads = []

        def fake_prepare(**kwargs):
            prepare_calls.append(kwargs)
            return {"subject": "subject", "html": "<html>Accept Invitation</html>", "text": "Accept Invitation"}

        def fake_deliver(*, email, email_payload):
            delivered_payloads.append((email, email_payload))
            return {"sent": False, "status": invitations.EMAIL_STATUS_NOT_CONFIGURED}

        invitations._prepare_invitation_email = fake_prepare
        invitations._deliver_invitation_email = fake_deliver
        invitations.current_user = lambda: {"id": "commissioner-user", "email": "commish@example.com"}
        try:
            fake = FakeClient()
            out = io.StringIO()
            with redirect_stdout(out):
                created = invitations.create_invitation(
                    league_id="league-1",
                    league_team_id="team-1",
                    email="owner@example.com",
                    sb=fake,
                    base_url="https://legacy.example/invite",
                )

                fake.select_invitation_rows = [dict(fake.update_base)]
                resent = invitations.resend_invitation(
                    "invite-1",
                    sb=fake,
                    base_url="https://legacy.example/invite",
                )

            self.assertTrue(created["ok"])
            self.assertTrue(resent["ok"])
            self.assertEqual(len(prepare_calls), 2)
            self.assertEqual(len(delivered_payloads), 2)
            self.assertEqual(delivered_payloads[0][1], {"subject": "subject", "html": "<html>Accept Invitation</html>", "text": "Accept Invitation"})
            self.assertNotIn("invite_token=", out.getvalue())
        finally:
            invitations._prepare_invitation_email = original_prepare
            invitations._deliver_invitation_email = original_deliver


class InvitationStatusAndSessionHelpersTest(unittest.TestCase):
    def test_classifies_active_pending_with_timezone_aware_datetime(self):
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        invite = {"status": "pending", "expires_at": now + timedelta(hours=1)}

        self.assertEqual(invitations.classify_invite_status(invite, now=now), "pending_active")

    def test_classifies_expired_pending_with_iso_string(self):
        now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        invite = {"status": "pending", "expires_at": "2026-07-19T23:59:00+00:00"}

        self.assertEqual(invitations.classify_invite_status(invite, now=now), "expired")

    def test_classifies_revoked_and_accepted(self):
        self.assertEqual(invitations.classify_invite_status({"status": "revoked"}), "revoked")
        self.assertEqual(invitations.classify_invite_status({"status": "accepted"}), "accepted")

    def test_malformed_expiration_does_not_crash_or_show_active(self):
        invite = {"status": "pending", "expires_at": "not-a-date"}

        self.assertEqual(invitations.classify_invite_status(invite), "expired")

    def test_invite_link_key_is_stable_and_collision_free(self):
        first = invitations.invite_link_session_key(
            league_id="league-1",
            league_team_id="team-1",
            invited_role="owner",
            invitation_id="invite-1",
        )
        same = invitations.invite_link_session_key(
            league_id="league-1",
            league_team_id="team-1",
            invited_role="owner",
            invitation_id="invite-1",
        )
        different_role = invitations.invite_link_session_key(
            league_id="league-1",
            league_team_id="team-1",
            invited_role="co_owner",
            invitation_id="invite-1",
        )

        self.assertEqual(first, same)
        self.assertNotEqual(first, different_role)

    def test_resend_replaces_retained_link_and_payload_is_safe(self):
        session_state = {}
        payload = {
            "invitation_id": "invite-1",
            "league_id": "league-1",
            "league_team_id": "team-1",
            "invited_role": "owner",
            "email": "owner@example.com",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "code": "created",
            "invite_url": "https://app.example?invite_token=first",
            "token_hash": "secret-hash",
            "credentials": "secret",
        }

        key = invitations.remember_invite_link(session_state, payload)
        invitations.remember_invite_link(
            session_state,
            {**payload, "code": "resent", "invite_url": "https://app.example?invite_token=second"},
        )

        self.assertEqual(session_state[key]["invite_url"], "https://app.example?invite_token=second")
        self.assertNotIn("token_hash", json.dumps(session_state[key]))
        self.assertNotIn("credentials", json.dumps(session_state[key]))

    def test_revoke_and_connected_status_clear_retained_links(self):
        session_state = {}
        payload = {
            "invitation_id": "invite-1",
            "league_id": "league-1",
            "league_team_id": "team-1",
            "invited_role": "owner",
            "email": "owner@example.com",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "code": "created",
            "invite_url": "https://app.example?invite_token=first",
        }

        invitations.remember_invite_link(session_state, payload)
        invitations.clear_invite_links(
            session_state,
            league_id="league-1",
            league_team_id="team-1",
            invited_role="owner",
            invitation_id="invite-1",
        )
        self.assertEqual(session_state, {})

        invitations.remember_invite_link(session_state, payload)
        invitations.clear_invite_links(
            session_state,
            league_id="league-1",
            league_team_id="team-1",
            invited_role="owner",
        )
        self.assertEqual(session_state, {})

    def test_connected_team_blocks_owner_and_limits_co_owner(self):
        connected = [{"role": "owner", "user_id": "user-1"}]
        active_co_owner = [
            {"role": "co_owner", "status": "pending", "expires_at": "2099-01-01T00:00:00+00:00"}
        ]

        self.assertFalse(
            invitations.can_offer_invite_role(
                connected_members=connected,
                active_invites=[],
                invite_role="owner",
            )
        )
        self.assertTrue(
            invitations.can_offer_invite_role(
                connected_members=connected,
                active_invites=[],
                invite_role="co_owner",
            )
        )
        self.assertFalse(
            invitations.can_offer_invite_role(
                connected_members=connected,
                active_invites=active_co_owner,
                invite_role="co_owner",
            )
        )


class InviteOnboardingHelpersTest(unittest.TestCase):
    def test_invite_token_is_captured_from_query_parameters(self):
        token = "valid-token_12345678901234567890"
        session_state = {}

        captured = invitations.capture_invite_token({"invite_token": token}, session_state)

        self.assertEqual(captured, token)
        self.assertEqual(session_state["pending_invite_token"], token)

    def test_invite_token_survives_reruns_and_auth_mode_switches(self):
        token = "valid-token_12345678901234567890"
        session_state = {"pending_invite_token": token, "auth_mode": "create"}

        captured = invitations.capture_invite_token({}, session_state)

        self.assertEqual(captured, token)
        self.assertEqual(session_state["auth_mode"], "create")

    def test_invite_token_survives_failed_login_until_cleared(self):
        token = "valid-token_12345678901234567890"
        session_state = {"pending_invite_token": token}

        self.assertEqual(invitations.capture_invite_token({}, session_state), token)
        invitations.clear_invite_onboarding_state(session_state)

        self.assertNotIn("pending_invite_token", session_state)

    def test_short_password_is_rejected_before_signup(self):
        result = invitations.validate_signup_inputs(
            email="owner@example.com",
            password="TB",
            password_confirm="TB",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "password_too_short")
        self.assertEqual(result["message"], "Password must be at least 6 characters.")

    def test_mismatched_passwords_are_rejected_precisely(self):
        result = invitations.validate_signup_inputs(
            email="owner@example.com",
            password="valid123",
            password_confirm="different123",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "password_mismatch")
        self.assertEqual(result["message"], "Passwords do not match.")

    def test_invalid_email_is_rejected_before_signup(self):
        result = invitations.validate_signup_inputs(
            email="owner-at-example",
            password="valid123",
            password_confirm="valid123",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_email")

    def test_valid_signup_inputs_normalize_email_and_reach_signup_gate(self):
        result = invitations.validate_signup_inputs(
            email="  OWNER@Example.COM ",
            password="valid123",
            password_confirm="valid123",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["email"], "owner@example.com")

    def test_existing_account_signup_error_directs_to_login(self):
        result = invitations.map_signup_exception(Exception("User already registered"))

        self.assertEqual(result["code"], "account_exists")
        self.assertIn("Switch to Log In", result["message"])

    def test_signup_session_branching_controls_acceptance(self):
        authenticated = {"ok": True, "has_user": True, "has_session": True}
        confirmation_required = {"ok": True, "has_user": True, "has_session": False}
        failed = {"ok": False, "has_user": False, "has_session": False}

        self.assertEqual(invitations.signup_result_state(authenticated), "authenticated")
        self.assertEqual(invitations.signup_result_state(confirmation_required), "confirmation_required")
        self.assertEqual(invitations.signup_result_state(failed), "failed")
        self.assertTrue(invitations.should_attempt_invite_acceptance(authenticated, {"id": "user-1"}))
        self.assertFalse(invitations.should_attempt_invite_acceptance(confirmation_required, None))

    def test_invite_token_survives_signup_validation_api_and_confirmation_states(self):
        token = "valid-token_12345678901234567890"
        session_state = {"pending_invite_token": token}

        invitations.validate_signup_inputs(
            email="owner@example.com",
            password="TB",
            password_confirm="TB",
        )
        self.assertEqual(session_state["pending_invite_token"], token)

        invitations.map_signup_exception(Exception("User already registered"))
        self.assertEqual(session_state["pending_invite_token"], token)

        self.assertEqual(
            invitations.signup_result_state({"ok": True, "has_user": True, "has_session": False}),
            "confirmation_required",
        )
        self.assertEqual(session_state["pending_invite_token"], token)

    def test_successful_acceptance_clear_helper_removes_invite_token(self):
        session_state = {
            "pending_invite_token": "valid-token_12345678901234567890",
            "pending_invite_preview": {"preview": {"status": "pending_active"}},
            "pending_invite_accepted": True,
        }

        invitations.clear_invite_onboarding_state(session_state)

        self.assertNotIn("pending_invite_token", session_state)
        self.assertNotIn("pending_invite_preview", session_state)
        self.assertNotIn("pending_invite_accepted", session_state)

    def test_sanitized_diagnostics_exclude_secrets_and_raw_tokens(self):
        result = invitations.sanitized_invite_diagnostics(
            has_user=True,
            has_session=False,
            acceptance_code="wrong_email",
            raw_token="valid-token_12345678901234567890",
            token_hash="secret",
            access_token="secret",
            refresh_token="secret",
            service_role_key="secret",
        )
        serialized = json.dumps(result)

        self.assertEqual(result["has_user"], True)
        self.assertEqual(result["has_session"], False)
        self.assertEqual(result["acceptance_code"], "wrong_email")
        self.assertNotIn("valid-token", serialized)
        self.assertNotIn("secret", serialized)

    def test_preview_returns_safe_invitation_data(self):
        token = "valid-token_12345678901234567890"
        fake = FakeClient()
        fake.select_invitation_rows = [
            {
                "id": "invite-1",
                "league_id": "league-1",
                "league_team_id": "team-1",
                "email": "Owner@Example.com",
                "role": "owner",
                "status": "pending",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "token_hash": invitations.hash_invite_token(token),
            }
        ]

        result = invitations.preview_invitation(token, sb=fake)
        serialized = json.dumps(result)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "pending_active")
        self.assertEqual(result["email"], "Owner@Example.com")
        self.assertNotIn("token_hash", serialized)
        self.assertNotIn(token, serialized)

    def test_preview_handles_expired_revoked_and_accepted_states(self):
        fake = FakeClient()

        for status, expected in [
            ("pending", "expired"),
            ("revoked", "revoked"),
            ("accepted", "accepted"),
        ]:
            fake.select_invitation_rows = [
                {
                    "id": "invite-1",
                    "league_id": "league-1",
                    "league_team_id": "team-1",
                    "email": "owner@example.com",
                    "role": "owner",
                    "status": status,
                    "expires_at": "2000-01-01T00:00:00+00:00",
                }
            ]

            result = invitations.preview_invitation("valid-token_12345678901234567890", sb=fake)
            self.assertEqual(result["status"], expected)

    def test_successful_acceptance_result_sets_league_team_and_role(self):
        fake = FakeClient()
        token = "valid-token_12345678901234567890"

        result = invitations.accept_invitation(token, sb=fake)

        self.assertTrue(result["ok"])
        self.assertEqual(result["league_id"], "league-1")
        self.assertEqual(result["league_team_id"], "team-1")
        self.assertEqual(result["role"], "member")


class InvitationCreateTest(unittest.TestCase):
    def setUp(self):
        invitations.current_user = lambda: {"id": "commissioner-user", "email": "commish@example.com"}

    def test_create_rejects_non_commissioner(self):
        fake = FakeClient()
        fake.is_commissioner = False

        result = invitations.create_invitation(
            league_id="league-1",
            league_team_id="team-1",
            email="owner@example.com",
            sb=fake,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "permission_denied")

    def test_create_rejects_team_outside_league(self):
        fake = FakeClient()
        fake.team_valid = False

        result = invitations.create_invitation(
            league_id="league-1",
            league_team_id="wrong-team",
            email="owner@example.com",
            sb=fake,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_team")

    def test_create_payload_and_result_are_safe(self):
        fake = FakeClient()

        result = invitations.create_invitation(
            league_id="league-1",
            league_team_id="team-1",
            email=" OWNER@example.com ",
            sb=fake,
            base_url="https://app.example/accept",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "created")
        self.assertEqual(result["email"], "owner@example.com")
        self.assertIn("invite_token=", result["invite_url"])
        self.assertFalse(result["email_sent"])
        self.assertEqual(result["email_status"], "not_configured")

        payload = fake.inserted[0]
        self.assertEqual(payload["send_count"], 0)
        self.assertIsNone(payload["last_sent_at"])
        self.assertIsNone(payload["token"])
        self.assertNotEqual(payload["token_hash"], "raw-token")
        self.assertEqual(len(payload["token_hash"]), 64)
        self.assertNotIn("token_hash", json.dumps(result))

    def test_expired_pending_is_revoked_before_replacement(self):
        fake = FakeClient()

        invitations.create_invitation(
            league_id="league-1",
            league_team_id="team-1",
            email="owner@example.com",
            sb=fake,
        )

        revoke_updates = [u for u in fake.updated if u[0].get("status") == "revoked"]
        self.assertTrue(revoke_updates)

    def test_active_pending_returns_structured_result(self):
        fake = FakeClient()
        fake.pending_rows = [
            {
                "id": "existing",
                "email": "owner@example.com",
                "league_id": "league-1",
                "league_team_id": "team-1",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "status": "pending",
            }
        ]

        result = invitations.create_invitation(
            league_id="league-1",
            league_team_id="team-1",
            email="owner@example.com",
            sb=fake,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "already_pending")
        self.assertEqual(result["invitation_id"], "existing")
        self.assertFalse(fake.inserted)

    def test_list_returns_listed_result_code(self):
        fake = FakeClient()

        result = invitations.list_team_invitations(league_id="league-1", sb=fake)

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "listed")
        self.assertIn("invitations", result)


class InvitationResendRevokeAcceptTest(unittest.TestCase):
    def setUp(self):
        invitations.current_user = lambda: {"id": "commissioner-user", "email": "commish@example.com"}

    def test_resend_replaces_hash_without_claiming_email_sent(self):
        fake = FakeClient()
        fake.select_invitation_rows = [dict(fake.update_base)]
        old_hash = fake.update_base["token_hash"]

        result = invitations.resend_invitation("invite-1", sb=fake, base_url="https://app.example")

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "resent")
        self.assertFalse(result["email_sent"])
        self.assertEqual(result["email_status"], "not_configured")
        self.assertIn("invite_token=", result["invite_url"])

        update_payload = fake.updated[-1][0]
        self.assertIn("token_hash", update_payload)
        self.assertNotEqual(update_payload["token_hash"], old_hash)
        self.assertNotIn("send_count", update_payload)
        self.assertNotIn("last_sent_at", update_payload)
        self.assertIsNone(update_payload["token"])
        self.assertNotIn("token_hash", json.dumps(result))

    def test_resend_conflict_returns_stable_result(self):
        fake = FakeClient()
        fake.select_invitation_rows = [dict(fake.update_base)]
        fake.update_conflict = True

        result = invitations.resend_invitation("invite-1", sb=fake)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "conflict")

    def test_revoke_requires_commissioner_and_marks_pending_revoked(self):
        fake = FakeClient()
        fake.select_invitation_rows = [dict(fake.update_base)]

        result = invitations.revoke_invitation("invite-1", sb=fake)

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "revoked")
        self.assertEqual(fake.updated[-1][0]["status"], "revoked")

        denied = FakeClient()
        denied.select_invitation_rows = [dict(denied.update_base)]
        denied.is_commissioner = False
        denied_result = invitations.revoke_invitation("invite-1", sb=denied)
        self.assertFalse(denied_result["ok"])
        self.assertEqual(denied_result["code"], "permission_denied")

    def test_acceptance_calls_rpc_with_only_raw_token(self):
        fake = FakeClient()
        token = "valid-token_12345678901234567890"

        result = invitations.accept_invitation(token, sb=fake)

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "accepted")
        self.assertEqual(fake.rpc_calls, [("accept_league_invite", {"raw_token": token})])
        self.assertNotIn("token_hash", json.dumps(result))

    def test_acceptance_parses_dict_rpc_response(self):
        fake = FakeClient()
        fake.rpc_data = {
            "league_id": "league-1",
            "league_team_id": "team-1",
            "membership_id": "membership-accepted",
            "role": "member",
            "team_name": "Assigned Team",
        }

        result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

        self.assertTrue(result["ok"])
        self.assertEqual(result["league_team_id"], "team-1")
        self.assertEqual(result["role"], "member")

    def test_empty_rpc_response_maps_to_invalid_link(self):
        fake = FakeClient()
        fake.rpc_data = []

        result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invitation_not_found")

    def test_acceptance_maps_rpc_failures_without_raw_database_text(self):
        fake = FakeClient()
        fake.rpc_error = "Invitation email does not match authenticated user"

        result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "wrong_email")
        self.assertNotIn("authenticated user", result["message"].lower())

    def test_api_error_exception_sanitization_is_printed(self):
        fake = FakeClient()
        fake.rpc_error = FakeApiError(
            code="42702",
            message="column reference league_id is ambiguous",
            details="raw token valid-token_12345678901234567890 should not leak",
            hint="Use a qualified column reference.",
        )

        out = io.StringIO()
        with redirect_stdout(out):
            result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

        diagnostics = out.getvalue()
        self.assertFalse(result["ok"])
        self.assertIn("INVITE_DEBUG rpc_exception_class=FakeApiError", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_code=42702", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_message=column reference league_id is ambiguous", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_details=raw token [redacted] should not leak", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_hint=Use a qualified column reference.", diagnostics)
        self.assertNotIn("valid-token_12345678901234567890", diagnostics)

    def test_dict_exception_sanitization_is_printed(self):
        fake = FakeClient()
        fake.rpc_error = Exception(
            {
                "code": "P0001",
                "message": "Invitation not found",
                "details": "access_token=secret-token-value",
                "hint": None,
            }
        )

        out = io.StringIO()
        with redirect_stdout(out):
            result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

        diagnostics = out.getvalue()
        self.assertFalse(result["ok"])
        self.assertIn("INVITE_DEBUG rpc_exception_code=P0001", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_message=Invitation not found", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_details=access_token=[redacted]", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_hint=none", diagnostics)

    def test_plain_exception_sanitization_is_printed(self):
        fake = FakeClient()
        fake.rpc_error = "plain database problem refresh_token=refresh-secret"

        out = io.StringIO()
        with redirect_stdout(out):
            result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

        diagnostics = out.getvalue()
        self.assertFalse(result["ok"])
        self.assertIn("INVITE_DEBUG rpc_exception_class=Exception", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_code=none", diagnostics)
        self.assertIn("INVITE_DEBUG rpc_exception_message=plain database problem refresh_token=[redacted]", diagnostics)
        self.assertNotIn("refresh-secret", diagnostics)

    def test_acceptance_maps_known_rpc_failures(self):
        cases = [
            ("Authentication required", "authentication_required"),
            ("Invitation not found", "invitation_not_found"),
            ("Invitation has expired", "invitation_expired"),
            ("Invitation has been revoked", "invitation_revoked"),
            ("Invitation already accepted", "invitation_already_accepted"),
            ("Invitation team is invalid", "invalid_team"),
        ]

        for message, code in cases:
            fake = FakeClient()
            fake.rpc_error = message

            result = invitations.accept_invitation("valid-token_12345678901234567890", sb=fake)

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], code)

    def test_results_do_not_expose_credentials_or_token_hash(self):
        fake = FakeClient()

        result = invitations.create_invitation(
            league_id="league-1",
            league_team_id="team-1",
            email="owner@example.com",
            sb=fake,
        )

        serialized = json.dumps(result)
        self.assertNotIn("token_hash", serialized)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", serialized)
        self.assertNotIn("service_role", serialized.lower())


class MigrationStaticTest(unittest.TestCase):
    def test_migration_hardening_markers(self):
        sql = MIGRATION.read_text()

        self.assertIn("extensions.digest", sql)
        self.assertIn("pg_catalog.encode", sql)
        self.assertIn("set search_path = pg_catalog, extensions", sql)
        self.assertIn("comment on column public.league_memberships.team_id", sql)
        self.assertIn("add column if not exists league_team_id", sql.lower())
        self.assertIn("league_invites_commissioner_insert", sql)
        self.assertIn("coalesce(with_check, '') in ('true', '(true)')", sql)
        self.assertNotIn("delete from public.league_invites", sql.lower())


class StageFourStaticTest(unittest.TestCase):
    def test_my_team_resolution_uses_canonical_league_team_id(self):
        app_context = (ROOT / "services" / "app_context.py").read_text()
        my_team_page = (ROOT / "pages" / "02_My_Team.py").read_text()
        my_team_context = (ROOT / "services" / "my_team_context.py").read_text()

        self.assertIn('membership.get("league_team_id") or membership.get("team_id")', app_context)
        self.assertIn("from services.my_team_context import resolve_my_team", my_team_page)
        self.assertIn('team_id = membership.get("league_team_id")', my_team_context)
        self.assertNotIn('membership.get("team_id")', my_team_context)

    def test_home_uses_existing_invite_query_parameter_and_acceptance_rpc(self):
        home = (ROOT / "home.py").read_text()

        self.assertIn("capture_invite_token(st.query_params", home)
        self.assertIn("accept_invitation(token, sb=_sb(access))", home)
        self.assertIn("clear_invite_query_params(st.query_params)", home)
        self.assertIn("if not user or not access", home)
        self.assertIn("def route_after_invite_acceptance", home)
        self.assertIn("Your invitation was accepted, but the app could not open your team.", home)

    def test_acceptance_guard_prevents_duplicate_rerun_call_and_allows_retry(self):
        home = (ROOT / "home.py").read_text()
        acceptance_block = home[home.index("def process_pending_invitation_after_auth"):home.index("def accept_pending_invite_if_ready")]

        self.assertIn("INVITE_ACCEPTANCE_IN_PROGRESS_KEY", home)
        self.assertIn("INVITE_ACCEPTANCE_LAST_FAILURE_KEY", home)
        self.assertIn('last_failure and last_failure.get("token") == token', home)
        self.assertIn('invite_debug("acceptance_rpc_called", "false")', home)
        self.assertIn('st.button("Try accepting invite again"', home)
        self.assertIn("st.session_state.pop(INVITE_ACCEPTANCE_LAST_FAILURE_KEY, None)", home)
        self.assertIn('st.session_state[INVITE_ACCEPTANCE_LAST_FAILURE_KEY] = {', home)
        self.assertLess(
            home.index('if last_failure and last_failure.get("token") == token'),
            home.index('result = accept_invitation(token, sb=authenticated_client)'),
        )
        self.assertLess(
            acceptance_block.index("st.session_state.pop(INVITE_ACCEPTANCE_IN_PROGRESS_KEY, None)"),
            acceptance_block.index("if result.get(\"ok\"):"),
        )

    def test_rpc_sql_contains_ambiguous_output_name_markers(self):
        sql = MIGRATION.read_text()
        function_sql = sql[sql.index("create or replace function public.accept_league_invite"):sql.index("revoke all on function public.accept_league_invite")]

        self.assertIn("returns table (\n    league_id uuid,", function_sql)
        self.assertIn("where id = invite_row.league_team_id\n      and league_id = invite_row.league_id", function_sql)
        self.assertIn("league_id := invite_row.league_id;", function_sql)
        self.assertIn("role := saved_membership.role;", function_sql)

    def test_post_login_invite_processing_precedes_membership_restore(self):
        home = (ROOT / "home.py").read_text()
        logged_in_block = home[home.index("if is_logged_in():"):home.index("# ============================================================\n# Logged-out state")]

        self.assertLess(
            logged_in_block.index("process_pending_invitation_after_auth()"),
            logged_in_block.index("restore_user_league()"),
        )
        self.assertLess(
            logged_in_block.index("process_pending_invitation_after_auth()"),
            logged_in_block.index("go_to_setup()"),
        )
        self.assertIn('if invite_decision == "blocked":\n            st.stop()', logged_in_block)

    def test_acceptance_success_sets_league_team_state_before_clearing_token(self):
        home = (ROOT / "home.py").read_text()
        apply_block = home[home.index("def apply_accepted_invite_result"):home.index("def process_pending_invitation_after_auth")]

        self.assertIn('st.session_state["active_league_id"]', apply_block)
        self.assertIn('st.session_state["league_team_id"]', apply_block)
        self.assertIn('st.session_state["active_team_id"]', apply_block)
        self.assertLess(
            apply_block.index('st.session_state["league_team_id"]'),
            apply_block.index("clear_invite_onboarding_state"),
        )

    def test_league_setup_bypasses_email_lookup_when_token_exists(self):
        setup = (ROOT / "pages" / "00_league_Setup.py").read_text()

        self.assertIn("INVITE_TOKEN_SESSION_KEY", setup)
        self.assertIn('st.session_state.pop("app_mode", None)', setup)
        self.assertIn('st.switch_page("home.py")', setup)
        self.assertLess(
            setup.index("if st.session_state.get(INVITE_TOKEN_SESSION_KEY):"),
            setup.index("pending_invites = get_pending_invites(user_email)"),
        )

    def test_home_has_forgot_password_only_in_login_flow_and_preserves_invite(self):
        home = (ROOT / "home.py").read_text()

        self.assertIn("def render_forgot_password", home)
        self.assertIn("invite_login_forgot_password", home)
        self.assertIn("login_forgot_password", home)
        self.assertNotIn("clear_invite_onboarding_state(st.session_state)\n        result = reset_password", home)

    def test_auth_exposes_signup_session_branch_for_invite_flow(self):
        auth = (ROOT / "auth.py").read_text()

        self.assertIn("def sign_up_with_result", auth)
        self.assertIn('"has_session": bool(session)', auth)
        self.assertIn('"has_user": bool(user)', auth)
        self.assertIn("client.postgrest.auth(effective_access_token)", auth)
        self.assertIn("client.auth.set_session(access_token, refresh_token)", auth)
        self.assertIn("def reset_password", auth)


if __name__ == "__main__":
    unittest.main()
