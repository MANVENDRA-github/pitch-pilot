# app (placeholder)

Reserved for a thin human-review front-end over pitch-pilot's review queue.

pitch-pilot never auto-sends: qualified leads are enqueued for review (see
`pitch_pilot.storage`). This `app/` directory is where a reviewer UI will live —
showing each drafted email next to the grounded facts and `source_url`s that back
it, so a human can approve, edit, or reject before anything is sent.

Empty in P0; scaffolded so the seam is explicit.
