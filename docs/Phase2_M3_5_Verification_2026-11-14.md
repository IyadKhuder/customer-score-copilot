# M3.5 delivery and recovery verification

Recorded 14 November 2026 from owner-observed hosted checks.

## Release and CI

- Candidate application revision: d1e8703.
- Release identity correction: 21604ba.
- Manually triggered Offline regression for 21604ba on
  phase2/persistent-review-workflow passed on 12 November.
- The original push-triggered run remained queued and could not be cancelled;
  the successful manual run resolved the CI acceptance gate.
- Existing candidate directory: /opt/scorecopilot/releases/m35-d1e8703.
- Initial image-store activation failure remains part of the verification history.
  Corrected activation succeeded without rebuilding the application image.

## Observed acceptance results

| Check | Result |
| --- | --- |
| Candidate startup | Explicit Compose startup with compose.yaml and release.override.json succeeded; services healthy. |
| HTTPS | Trusted public readiness request returned HTTP 200. |
| Original case | Saved package and decision readback passed before rollback. |
| Duplicate submission | Two calls using the same durable receipt returned the same case ID; state progressed from queued to awaiting_review. No analyst decision was submitted. |
| Explicit rollback | release.py rollback succeeded; services healthy. |
| Preservation after rollback | Trusted HTTPS returned 200; original saved package and decision readback passed. |
| Stop/start recovery | VM deallocated and restarted; explicit Compose startup with both files selected the recorded rollback image. |
| Preservation after restart | Trusted HTTPS returned 200; original saved package and decision readback passed. |
| Activation receipt | Saved locally under ignored var/azure; confirmed ignored and untracked. |
| Final shutdown | PowerState/deallocated; public IP and FQDN absent; guard disarmed. |
| Final inventory | Only VM, disk, NIC, VNet and NSG remained, all in Sweden Central. No public-IP resource. Disk remained 32 GiB, Standard_LRS. |

## Recorded image identities

Candidate API host image:
sha256:1c05dafbba172dedeafbe5d39bae602ceb7f85bc8e826a8b77234019c253b0fb

Final selected API image after rollback and restart:
sha256:d6394be39116d5edc77278832534036877038331ff216b6209cc2168d63c83a6

Proxy host image:
sha256:ce773ec7a742ce15ee6bf2bb5f65882b9e2a4487b10733e6f1484f86e6b5d8bc

These are local Docker image identities, not registry digests.

## Preserved case evidence

Original case: e1bd8ce0-76e0-4059-bb38-944fa475d9c8.

Original package SHA256:
19514e3444fb1906403ae381c23e6d225863d5851a6e5bda1e57742a7d5b3507

Duplicate-submission case: e09ee4fd-e06f-4a40-aa91-f14c734dd660.

Private receipts remain under ignored var/azure. The primary database was not
restored or replaced during rollback. The previous release intentionally remains
selected; no repeat deployment is required.

## Scope and closure

Hosted acceptance checks passed. Documentation commit, push and its CI result
remain to be recorded. No additional project hours or reconciled Azure cost are
inferred. Retained disk storage continues billing while the VM is deallocated.
The Slack-to-web bridge remains postponed. M4.1 is the next planned checkpoint.
