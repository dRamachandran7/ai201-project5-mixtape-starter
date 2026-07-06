## AI Usage

I used Claude Code throughout this project, with different levels of involvement per issue:

- **Issue 2 (feed_service.py):** I asked Claude Code to explain `get_friends_listening_now` and help me reproduce the bug. It gave me the `sqlite3` commands to list user/friendship IDs and listening-event timestamps, and the `curl` commands to hit `/feed/<user_id>/listening-now`. Since the seeded data didn't happen to include an event that landed in the exact "yesterday but under 24 hours old" window, it walked me through manually updating one event's `listened_at` via SQL to land in that window so the bug could be observed live. After I described the fix I wanted ("cutoff should land at midnight of the current day"), Claude Code wrote the actual code change in `feed_service.py` and authored `tests/test_feed.py`, including freezing time via a monkeypatched `datetime` so the "yesterday vs. today" tests aren't flaky depending on when they're run. It verified the new test by temporarily reverting to the original rolling-24-hour code, confirming the regression test failed against it, then restoring the fix.

- **Issue 1 (streak_service.py):** I asked Claude Code to explain the streak logic and point to the specific bug. It identified the `today.weekday() != 6` condition and explained why Sunday triggers the reset. Since reproducing this against a real Sunday wasn't practical, it gave me a Python script that calls `update_listening_streak` directly with a manually constructed Saturday-then-Sunday pair of timestamps to trigger the bug deterministically. When I asked it to fix Issue 1, Claude Code made the one-line code change (removing the weekday condition) and confirmed the existing `test_streak_increments_on_sunday` test, which failed before the fix, passed afterward.

- **Issue 3 (search_service.py):** I asked Claude Code to explain `search_songs`, and it described the classic SQLAlchemy "join fan-out" mechanism — joining `Song` to `song_tags` produces one row per `(song, tag)` pair, so a song with 3 tags should appear 3 times. When I asked it to double check this against the actual codebase, it ran the existing `tests/test_search.py` suite and found the duplicate did **not** actually reproduce with the installed SQLAlchemy/Flask-SQLAlchemy versions (their legacy `Query.all()` auto-deduplicates full-entity results by primary key). It flagged that its own initial theoretical explanation didn't hold up under the actual test run. I removed the unnecessary `outerjoin` myself based on that discussion, since the join wasn't needed for anything regardless of whether it was actively causing duplicates.

## Codebase map

- app.py: The Flask app factory. Creates the app, configures the SQLite database, and registers the four route blueprints (songs, playlists, users, feed).

- models.py: This defines 7 SQLAlchemy models: User, Tag, Song, ListeningEvent, Rating, Playlist, and Notification.

- routes/songs.py: Endpoints for searching songs, fetching a single song, logging a listen, and rating a song. Delegates to search_service.py, streak_service.py, and notification_service.py.

- routes/playlists.py: Endpoints for creating a playlist and adding/retrieving its songs. Delegates to playlist_service.py and notification_service.py.

- routes/users.py: Endpoints for a user's profile, streak, and notifications. Delegates to streak_service.py and notification_service.py.

- routes/feed.py: Endpoints for "friends listening now" and the general activity feed. Delegates to feed_service.py.

- services/streak_service.py: Records listening events and updates a user's day-over-day listening streak (`record_listening_event`, `update_listening_streak`).

- services/feed_service.py: Builds the "friends listening now" feed (filtered to today) and the unfiltered recent activity feed.

- services/search_service.py: Searches songs by title/artist (case-insensitive) and fetches a single song by ID.

- services/notification_service.py: Creates and retrieves notifications, and handles adding a song to a playlist plus rating a song (each of which can trigger a notification).

- services/playlist_service.py: Creates playlists and retrieves a playlist's songs in position order.

- seed_data.py: Populates the database with sample users, songs, playlists, listening events, and notifications for local testing.

## Data Flow example

In order to get the user's friend listening activity in their feed, the following occurs:

POST /<user_id>/listening-now in routes/feed.py calls get_friends_listening_now(user_id) from feed_service.py. That function finds all the user's friends that have a ListeningEvent within a predefined time cutoff from the present, and returns a list[dict].

# Issues and Fixes

---

## Issue 2: Friends Listening Now shows people from yesterday

- I was able to recreate this bug by first listing the user ids with the sqlite instance, then the listening events. I then found an event that occurred the day previous, but within 24 hours of the current time. Finally, I took that event's user_id and looked for another user that had a friend relationship with them, then listed their listening-now. I then found that a listening event from the day previous was indeed listed.

- To find the root cause, I first took a look at the feed.py route, the took a look at the feed_service.py file, and specifically the get_friends_listening_now function. I could see that was the function that is called to show friends that are listening now, and took a look through the docstring and logic, which exposed the problem, which was the way the cutoff was calculated.

- The root cause of this bug was, as aforementioned, the cutoff logic. Previously, the function used a strict 24 hour cutoff. This means that if a friend's most recent listening event was, say 23 hours ago, it would likely have been the previous day, but still be returned by the function. 

- To fix this, I simply changed the cutoff to use midnight of the current day to make sure that no events from the previous day occurred. Afterward, I had Claude Code write pytests to make sure that the issue did not occur again. I also made sure to run the full test suite again to make sure that all other services were running as expected.

## Issue 1: My listening streak keeps resetting

- To recreate this bug, I chose some user with a non '1' streak, and set their last_listened_at to be a saturday (6/26). Then, I triggered an update_listening_streak with 'now' as the following sunday, and their streak was updated to 1.

- To find the root cause, I started at users.py route, which led me to the streak_service.py file, and specifically the update_listening_streak function. Since this is the only function responsible for changing a user's streak, the reset bug must be occurring here.

- The root cause was an erroneous `today.weekday() != 6` condition on the `elif days_since_last == 1` branch (`update_listening_streak` in `services/streak_service.py`, before the fix below). `date.weekday()` returns 6 for Sunday, so this condition evaluated to `False` on Sundays, meaning the increment branch was skipped even when the user had listened on consecutive days. Execution fell through to the `else` clause, which reset `listening_streak` to 1. This means every Sunday, any user with an active streak would have it incorrectly reset instead of incremented.

- To fix this, I removed the `today.weekday() != 6` condition from the `elif days_since_last == 1` branch, so the streak increments on any consecutive day regardless of which day of the week it is. There was no legitimate reason for Sundays to be treated differently. `tests/test_streaks.py` already had a `test_streak_increments_on_sunday` test that captured this exact scenario and was failing before the fix; it now passes, and I re-ran the full test suite to confirm no other services regressed.

## Issue 3: The same song keeps showing up twice in search

- This has to do with the step where Song and song_tags are joined. If a song has multiple tags, then it will appear in multiple rows, since the columns were joined, and therefore will result in duplicates. To trigger this, I took a look at the song and song tags joined table to see the duplicates.

- I started by looking at the songs.py route, then the search_service.py service and specifically the search function. I could see then that the joining of the two mentioned columns in the database might be causing issues.

- As mentioned, the root cause of this is joining the Song and Song tags columns. Songs with multiple tags will cause multiple instances of the same song to appear in the search as a result, which causes the bug.

- To fix, I simply removed the joining step in the db query, which removes all duplicates. I made sure to create tests where songs had multiple tags, and also ran the full test suite to make sure other systems were not affected.