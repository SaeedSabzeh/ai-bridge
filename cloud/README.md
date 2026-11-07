# Run AI Bridge when your PC is off

Run the same Windows application on a dedicated Google Compute Engine VM.
The VM is the project's writer and stores its own checkpoints. Connect from
Windows using Remote Desktop over Google Identity-Aware Proxy (IAP). There is
no public web dashboard, public VM address, or internet-facing RDP rule.

This deployment is a starting point for a single operator and small tasks.
Provisioning requires a billing-enabled Google Cloud project and the Google
Cloud CLI on your control PC. Review the [Compute Engine pricing calculator](https://cloud.google.com/products/calculator),
[Cloud NAT pricing](https://cloud.google.com/nat/pricing), and storage costs
before enabling anything. The plan includes compute, Windows licensing,
persistent storage, NAT and an external NAT address. Stopping the VM does not
remove its disk or network resources. Budget alerts do not cap spending.

## 1. Review and create the VM

Run from this repository on the control PC. Replace the example project and
zone. Start with a plan; it makes no Google Cloud calls.

```powershell
.\cloud\New-BridgeVm.ps1 -Project my-project-id -Zone europe-west8-b
```

If the project, capacity and cost are acceptable, use the same command with
`-Apply`. It enables Compute Engine and IAP, creates a dedicated network and
Cloud NAT for outbound access, then creates the VM. It does not modify existing
resources, open a public port, grant IAM access, or attach a Google Cloud
service account. If a step fails, inspect the listed resources in the console
before continuing; the script deliberately refuses to reuse name collisions.

## 2. Connect privately

In [IAP TCP forwarding](https://console.cloud.google.com/security/iap), grant
**IAP-secured Tunnel User** on this VM to your intended Google identity, with
the IAM condition `destination.port == 3389`. The operator also needs Compute
permissions to look up the VM; a separate administrator can create the initial
Windows login. Follow [Google's per-VM access instructions](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding#grant-access-to-a-specific-vm).

Use [Google's Windows credentials instructions](https://docs.cloud.google.com/compute/docs/instances/windows/generating-credentials)
to set a password directly in your account. Keep the Windows password out of
scripts and chat. Open the tunnel from your local terminal:

```powershell
gcloud compute start-iap-tunnel ai-bridge 3389 --project=my-project-id --zone=europe-west8-b --local-host-port=127.0.0.1:13389
```

Leave that terminal open. In Remote Desktop Connection, connect to
`127.0.0.1:13389` with the Windows account for the VM.

## 3. Install the app inside the VM

Build the source bundle locally with `.\cloud\New-BridgeBundle.ps1`. Transfer
only that zip through your authenticated RDP session, then extract it under a
folder owned by the Windows user who will run the bridge. The zip is built
from an explicit list of committed source files. It excludes your existing
transcripts, settings, project datasets, CLI downloads and credentials.

Inside the VM, install [Python for Windows](https://www.python.org/downloads/windows/)
with Tkinter, and [Git for Windows](https://gitforwindows.org/) for code
checkpoints. Install the official [Claude Code CLI](https://code.claude.com/docs/en/setup)
and [Codex CLI](https://developers.openai.com/codex/cli) for this Windows user.
In the extracted folder, use a regular PowerShell session:

```powershell
claude auth login
codex login
python ai_bridge.py doctor
python ai_bridge.py connect
```

Choose your Claude and ChatGPT subscription accounts in the sign-in prompts.
Sign in directly on the VM. Do not copy authentication files or set model API
keys. Verify both providers in **Connections** after opening **Launch AI
Bridge.cmd**. Register a new small workspace on the VM and approve only the
commands it needs. To move an existing project later, pause its local writer
and explicitly select the source and checkpoint material to transfer. Never
run the same writable project from both machines at once.

## 4. Test disconnect and recovery

1. On the VM, start a small task with a known passing check. Close the RDP
   window to **disconnect**; do not sign out. Reconnect and confirm the check
   finished or the same turn is still running. Check that no duplicate request
   was created.
2. For a disposable test only, verify the restart path too. After a VM restart
   or a Windows session ending mid-turn, sign in, open the task and use
   **Recover interrupted run**. Confirm the checkpoint is intact and no work
   was replayed. Then explicitly **Start / Resume**.

The VM can keep an active turn running while your PC is off as long as the VM
and its Windows session stay running. Check disconnected-session time limits
and the maintenance schedule on the actual VM. A reboot or sign-out can stop
an active turn. This setup does not store a password for auto-logon, change
Windows Update policy, or restart a possibly billed model request automatically.
The app runs one requested turn at a time; a paused turn stays paused until
you resume it. Use **Connections → CLI settings** to raise the turn limit for
longer approved checks.
