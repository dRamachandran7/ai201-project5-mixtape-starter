"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services import feed_service
from services.feed_service import get_friends_listening_now


# Frozen "current" moment used by every test: a Monday afternoon.
FAKE_NOW = datetime(2024, 6, 10, 15, 0, 0, tzinfo=timezone.utc)
TODAY_MIDNIGHT = datetime(2024, 6, 10, 0, 0, 0, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    """A datetime subclass whose .now() always returns FAKE_NOW."""

    @classmethod
    def now(cls, tz=None):
        return FAKE_NOW if tz else FAKE_NOW.replace(tzinfo=None)


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture(autouse=True)
def freeze_time(monkeypatch):
    """Pin feed_service's notion of "now" so date-boundary tests aren't flaky."""
    monkeypatch.setattr(feed_service, "datetime", FrozenDateTime)


@pytest.fixture
def seed_friends(app):
    """
    Create a user with three friends, each with a listening event at a
    different point relative to FAKE_NOW / the midnight boundary.
    """
    with app.app_context():
        me = User(username="me", email="me@example.com")
        recent_friend = User(username="recent_friend", email="recent@example.com")
        yesterday_friend = User(username="yesterday_friend", email="yesterday@example.com")
        old_friend = User(username="old_friend", email="old@example.com")
        db.session.add_all([me, recent_friend, yesterday_friend, old_friend])
        db.session.flush()

        def add_friendship(u1, u2):
            db.session.execute(friendships.insert().values(user_id=u1.id, friend_id=u2.id))
            db.session.execute(friendships.insert().values(user_id=u2.id, friend_id=u1.id))

        add_friendship(me, recent_friend)
        add_friendship(me, yesterday_friend)
        add_friendship(me, old_friend)

        song = Song(title="Test Song", artist="Test Artist", shared_by=me.id)
        db.session.add(song)
        db.session.flush()

        # Listened 1 hour ago, today — should appear.
        db.session.add(ListeningEvent(
            user_id=recent_friend.id, song_id=song.id,
            listened_at=FAKE_NOW - timedelta(hours=1),
        ))

        # Listened 30 minutes before midnight — yesterday's date, only ~15.5
        # hours ago. Well within the old 24-hour rolling window, but not
        # "today". This is the case the bug fix targets.
        db.session.add(ListeningEvent(
            user_id=yesterday_friend.id, song_id=song.id,
            listened_at=TODAY_MIDNIGHT - timedelta(minutes=30),
        ))

        # Listened 3 days ago — should never appear, before or after the fix.
        db.session.add(ListeningEvent(
            user_id=old_friend.id, song_id=song.id,
            listened_at=FAKE_NOW - timedelta(days=3),
        ))

        db.session.commit()
        yield {
            "me": me,
            "recent_friend": recent_friend,
            "yesterday_friend": yesterday_friend,
            "old_friend": old_friend,
            "song": song,
        }


def test_friend_listening_today_appears(app, seed_friends):
    """A friend who listened earlier today should appear in the feed."""
    with app.app_context():
        result = get_friends_listening_now(seed_friends["me"].id)
        usernames = [r["friend"]["username"] for r in result]
        assert "recent_friend" in usernames


def test_friend_listening_yesterday_is_excluded_even_within_24_hours(app, seed_friends):
    """
    Regression test for "Friends Listening Now shows people from yesterday".

    A friend who listened ~15.5 hours ago but before midnight (i.e. on the
    previous calendar date) should NOT appear, even though 15.5 hours is
    well within a rolling 24-hour window. Bug caused this friend to show up.
    """
    with app.app_context():
        result = get_friends_listening_now(seed_friends["me"].id)
        usernames = [r["friend"]["username"] for r in result]
        assert "yesterday_friend" not in usernames


def test_friend_listening_days_ago_is_excluded(app, seed_friends):
    """A friend who listened days ago should not appear."""
    with app.app_context():
        result = get_friends_listening_now(seed_friends["me"].id)
        usernames = [r["friend"]["username"] for r in result]
        assert "old_friend" not in usernames


def test_event_at_start_of_today_is_included(app, seed_friends):
    """An event timestamped exactly at midnight today should count as 'today'."""
    with app.app_context():
        midnight_friend = User(username="midnight_friend", email="midnight@example.com")
        db.session.add(midnight_friend)
        db.session.flush()
        db.session.execute(friendships.insert().values(
            user_id=seed_friends["me"].id, friend_id=midnight_friend.id
        ))
        db.session.execute(friendships.insert().values(
            user_id=midnight_friend.id, friend_id=seed_friends["me"].id
        ))
        db.session.add(ListeningEvent(
            user_id=midnight_friend.id, song_id=seed_friends["song"].id,
            listened_at=TODAY_MIDNIGHT,
        ))
        db.session.commit()

        result = get_friends_listening_now(seed_friends["me"].id)
        usernames = [r["friend"]["username"] for r in result]
        assert "midnight_friend" in usernames


def test_no_friends_returns_empty_list(app):
    """A user with no friends gets an empty feed."""
    with app.app_context():
        user = User(username="loner", email="loner@example.com")
        db.session.add(user)
        db.session.commit()
        assert get_friends_listening_now(user.id) == []
