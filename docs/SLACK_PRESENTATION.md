# Readable Slack message formatting

Added 2026-10-28, based on implementation checkpoint 6921472.

New replies use native Slack Block Kit formatting. The presentation layer adds
no factual content and performs no scoring, policy checks, database access or
provider requests. It receives only the already-composed public reply.

## Visual hierarchy

- Account identity, policy-reference titles and "Verified note" use native
  larger headings.
- Recovery conclusions and review outcomes are bold.
- Pattern, coverage, source, next-step and request-count labels are bold.
- Descriptive-window and scope qualifications use italics.
- Observed drops/rises, subsequent changes and net changes use bullets.
- Monthly observations remain one per line, grouped in a monospace block.
- Usage examples use inline code styling. Greetings and normal explanations
  retain normal text for easy reading.

Font families and rendering are controlled by Slack. The patch uses the normal
Slack font and native monospace where appropriate, rather than arbitrary fonts
or colors. Numbering is not added to a single next step. No horizontal dividers
or decorative emoji are inserted.

## Text and transport guarantees

The same escaped plain-text reply is retained in the top-level message text for
notifications, accessibility and fallback. Its text, ordering, existing message
splitting and routing are unchanged. The native blocks display the same content
with structural styling. Monthly data stays unchanged and charts are uploaded
separately to the same thread as before.

Names, policy excerpts and composed note text are native literal text elements;
they are not interpolated into markdown. The renderer never constructs user,
channel, broadcast or URL-link elements from these strings. Mention-like text,
asterisks, underscores and backtick fences therefore remain literal content.
The escaped top-level fallback continues to disable mrkdwn, link names and
unfurls. Publication gates still decide whether any note content can appear.

Conservative local size limits fall back to the original plain text when a
message is unsuitable for formatting. A local rendering error also sends plain
text. A network/Slack delivery error is not automatically retried, avoiding
ambiguous duplicate deliveries or repeated reviews/provider requests.

Only NEW messages are formatted. Restart the listener after applying the patch
and send new requests; existing Slack messages are not edited retroactively.

## Verification and references

Tests check headings, bold/italic labels, bullets, monthly code layout, literal
handling of untrusted text, unchanged visible wording, blocked-note exclusion,
long-message splitting, local formatting failure, same-thread delivery,
deduplication and SDK payload serialization. Existing live-note verification and
chart transport tests also run without live provider or Slack calls.

Slack references:
- [Native rich text](https://docs.slack.dev/reference/block-kit/blocks/rich-text-block/)
- [Headers](https://docs.slack.dev/reference/block-kit/blocks/header-block/)
- [Preformatted text](https://docs.slack.dev/reference/block-kit/block-elements/rich-text-preformatted-element/)
