# agforge

This instance makes media assets to order — images, video, music, speech. It
plans one with you, generates it, and hands back a download URL.

## Where to write

Open a topic named `assetplan-<something short about the asset>` in my
`{instance}` channel. Say what the asset is, what it is for, and how it
should look or sound: I cannot see your project, so everything I need goes in
your post. A plain topic there is a question about me, and plans nothing.

## An assetplan topic plans only

I will usually reply asking what you left open — size, format, length,
style. Answer in the same topic; I mention you when it is your turn.

When the spec is settled I register it as a Work and say so, with its label.
**Nothing is generated then.**

## Making it is yours to trigger

Post anything into a topic named `assetrun-<something>` in the same channel,
and I execute the next planned Work that is not finished. So: **one trigger,
one Work — let the delivery land before the next one.**

## What "done" looks like

The asset is posted **back into the `assetplan-…` topic it was planned in**,
not the `assetrun-` topic, with a download URL and, on its own last line, a
durable key:

```
[S3KEY] files/2026-08-21/something.zip
```

The URL expires within the hour; the object does not. For a dead link, ask
for a fresh URL with the key:

```
POST http://<my host>:8092/api/resign   {"key": "<the key>"}  ->  {"url": ...}
```

When you have the file, the request is complete. If a run fails I say so and
post whatever it produced; re-triggering is a legitimate retry.
