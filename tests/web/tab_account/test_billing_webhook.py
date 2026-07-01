"""B20.2/B20.3 tests: Stripe webhook mirror, idempotency ledger, seat sync +
downgrade archiving. Runs with HELGA_BILLING_TEST=true (unsigned JSON)."""

import os
import sys
import unittest
from unittest.mock import MagicMock

_here = os.path.dirname(__file__)
_root = os.path.abspath(os.path.join(_here, '../../../'))
_webui = os.path.join(_root, 'services/web-ui')
for p in (_root, _webui):
    if p not in sys.path:
        sys.path.insert(0, p)

for _mod in ("gevent", "gevent.monkey", "socketio", "flask_socketio"):
    if isinstance(sys.modules.get(_mod), MagicMock):
        del sys.modules[_mod]
        sys.modules.pop("app", None)
        sys.modules.pop("auth", None)

os.environ['HELGA_BILLING_TEST'] = 'true'
import app as webui_app  # noqa: E402

app = webui_app.app
app.config['TESTING'] = True
storage = webui_app._get_storage()


def _parent(email):
    client = app.test_client()
    r = client.post('/signup', json={'email': email, 'password': 'longenough1'})
    return client, r.get_json()['parent_id']


def _event(event_id, etype, obj):
    return {"id": event_id, "type": etype, "data": {"object": obj}}


class TestWebhookMirror(unittest.TestCase):
    def test_checkout_completed_activates(self):
        _, pid = _parent("bill1@example.com")
        r = app.test_client().post('/api/billing/webhook', json=_event(
            "evt_001", "checkout.session.completed",
            {"customer": "cus_1", "subscription": "sub_1",
             "client_reference_id": pid,
             "metadata": {"plan": "family_annual", "seats": "4"},
             "current_period_end": 1790000000}))
        assert r.status_code == 200
        sub = storage.subscriptions.get(pid)
        assert sub["status"] == "active" and sub["seats"] == 4
        assert sub["provider_sub_id"] == "sub_1"
        assert storage.subscriptions.seats_for(pid) == 4

    def test_idempotent_replay_is_noop(self):
        _, pid = _parent("bill2@example.com")
        ev = _event("evt_dup", "checkout.session.completed",
                    {"client_reference_id": pid,
                     "metadata": {"plan": "x", "seats": "2"}})
        assert app.test_client().post('/api/billing/webhook', json=ev).status_code == 200
        storage.subscriptions.upsert(pid, seats=9)   # mutate after first apply
        assert app.test_client().post('/api/billing/webhook', json=ev).status_code == 200
        assert storage.subscriptions.get(pid)["seats"] == 9  # replay didn't re-apply

    def test_payment_failed_past_due_and_notified(self):
        _, pid = _parent("bill3@example.com")
        app.test_client().post('/api/billing/webhook', json=_event(
            "evt_pf", "invoice.payment_failed", {"metadata": {"parent_id": pid}}))
        assert storage.subscriptions.get(pid)["status"] == "past_due"
        assert storage.notifications.unread_count(pid) >= 1

    def test_subscription_deleted_cancels(self):
        _, pid = _parent("bill4@example.com")
        app.test_client().post('/api/billing/webhook', json=_event(
            "evt_del", "customer.subscription.deleted",
            {"metadata": {"parent_id": pid}}))
        assert storage.subscriptions.get(pid)["status"] == "canceled"

    def test_seat_downgrade_archives_newest_students(self):
        client, pid = _parent("bill5@example.com")
        storage.subscriptions.upsert(pid, seats=3, status='active')
        ids = []
        for name in ("First", "Second", "Third"):
            r = client.post('/api/students', json={'display_name': name})
            ids.append(r.get_json()['student_id'])
        app.test_client().post('/api/billing/webhook', json=_event(
            "evt_down", "customer.subscription.updated",
            {"metadata": {"parent_id": pid}, "status": "active",
             "items": {"data": [{"quantity": 1}]}}))
        active = storage.accounts.list_students(pid)
        assert len(active) == 1
        assert active[0]["display_name"] == "First"   # newest archived first
        archived = [s for s in storage.accounts.list_students(pid, include_archived=True)
                    if s["status"] == "archived"]
        assert len(archived) == 2                      # archived, never deleted

    def test_unhandled_event_returns_200(self):
        r = app.test_client().post('/api/billing/webhook', json=_event(
            "evt_misc", "customer.created", {}))
        assert r.status_code == 200


if __name__ == '__main__':
    unittest.main()
