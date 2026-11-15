import os
from datetime import datetime, timedelta, timezone

import boto3

# ---------------------------------------------------------------
# BLOCK 1: Settings
# Read from Lambda environment variables (Configuration > Environment variables).
# The value after the comma is only used if the variable is not set.
# ---------------------------------------------------------------
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"    # true = only log, never delete
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "0"))     # snapshots newer than this are kept
PROTECT_TAG_KEY = os.environ.get("PROTECT_TAG_KEY", "DoNotDelete")  # tag key that protects a snapshot

ec2 = boto3.client("ec2")  # created once and reused across invocations


# ---------------------------------------------------------------
# BLOCK 2: Helper to fetch ALL results
# AWS returns results in pages. A paginator keeps asking for the next
# page, so large accounts are not silently cut off.
# ---------------------------------------------------------------
def get_all(api_name, result_key, **filters):
    items = []
    for page in ec2.get_paginator(api_name).paginate(**filters):
        items += page[result_key]
    return items


def lambda_handler(event, context):
    # ---------------------------------------------------------------
    # BLOCK 3: Prepare lookup data (one call each, not one per snapshot)
    # ---------------------------------------------------------------
    # Oldest snapshot date we are allowed to delete
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)

    # {volume_id: True/False} -> is the volume attached to an instance?
    # A volume on a running OR stopped instance still has an attachment,
    # so stopped instances are protected automatically.
    volume_is_attached = {}
    for volume in get_all("describe_volumes", "Volumes"):
        volume_is_attached[volume["VolumeId"]] = bool(volume["Attachments"])
    

    # Snapshot IDs used by AMIs we own (AWS refuses to delete these anyway)
    ami_snapshot_ids = set()
    for image in get_all("describe_images", "Images", Owners=["self"]):
        for device in image.get("BlockDeviceMappings", []):
            if "SnapshotId" in device.get("Ebs", {}):
                ami_snapshot_ids.add(device["Ebs"]["SnapshotId"])

    counts = {"scanned": 0, "deleted": 0, "skipped": 0, "errors": 0}

    # ---------------------------------------------------------------
    # BLOCK 4: Check every snapshot and decide: keep or delete
    # ---------------------------------------------------------------
    for snapshot in get_all("describe_snapshots", "Snapshots", OwnerIds=["self"]):
        snapshot_id = snapshot["SnapshotId"]
        volume_id = snapshot.get("VolumeId")
        tag_keys = [tag["Key"] for tag in snapshot.get("Tags", [])]
        counts["scanned"] += 1

        # Reasons to KEEP the snapshot (first match wins)
        if snapshot["StartTime"] > cutoff_date:
            keep_reason = f"newer than {RETENTION_DAYS} days"
        elif PROTECT_TAG_KEY in tag_keys:
            keep_reason = f"has protection tag '{PROTECT_TAG_KEY}'"
        elif snapshot_id in ami_snapshot_ids:
            keep_reason = "used by an AMI"
        elif volume_is_attached.get(volume_id):
            # Only True when the volume exists AND is attached.
            # No volume id, deleted volume, or unattached volume -> None/False -> delete.
            keep_reason = "volume is attached to an instance"
        else:
            keep_reason = None

        if keep_reason:
            print(f"KEEP   {snapshot_id}: {keep_reason}")
            counts["skipped"] += 1
            continue

        # ---------------------------------------------------------------
        # BLOCK 5: Delete (or only log when DRY_RUN is true)
        # ---------------------------------------------------------------
        if DRY_RUN:
            print(f"DRY RUN - would delete {snapshot_id} (volume: {volume_id})")
            counts["deleted"] += 1
            continue

        try:
            ec2.delete_snapshot(SnapshotId=snapshot_id)
            print(f"DELETED {snapshot_id} (volume: {volume_id})")
            counts["deleted"] += 1
        except ec2.exceptions.ClientError as error:
            # One failing snapshot should not stop the whole run
            print(f"ERROR  {snapshot_id}: {error}")
            counts["errors"] += 1

    # ---------------------------------------------------------------
    # BLOCK 6: Summary (shown in the test result and CloudWatch Logs)
    # ---------------------------------------------------------------
    print(f"Summary (dry_run={DRY_RUN}): {counts}")
    return {"dry_run": DRY_RUN, **counts}
