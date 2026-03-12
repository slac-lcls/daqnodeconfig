#!/usr/bin/env python3
"""
Check if a folder/mount point exists across multiple nodes using clush.
"""

import subprocess
import sys
import re
from typing import Dict, Tuple

# Configuration
NODES = []
for i in range(1,57):
    NODES.append(f"drp-srcf-cmp{i:03d}")     
for i in range(1,11):
    NODES.append(f"drp-srcf-mon{i:03d}")
for i in range(1,6):
    NODES.append(f"drp-srcf-gpu{i:03d}")


MOUNT_POINT = "/cds/drpsrcf/"  # Change to your mount point
SSH_USER = None  # Set to username if different from current user


def check_mount_with_clush(nodes: list, mount_point: str) -> Tuple[Dict[str, bool], Dict[str, str]]:
    """
    Check if a mount point exists on remote nodes using clush.
    
    Args:
        nodes: List of hostnames or IPs
        mount_point: Path to check
        ssh_user: SSH username (None for current user)
    
    Returns:
        Tuple of (mount_status_dict, message_dict)
    """
    node_list = ",".join(nodes)
    
    # Build clush command
    cmd = ["clush", "-w", node_list, "-b"]
    
    
    # Check if mount point is mounted
    cmd.append(f"mountpoint -q {mount_point} && echo 'MOUNTED' || echo 'NOT_MOUNTED'")
    
    mount_status = {}
    messages = {}
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # Parse clush output
        # Format is typically:
        # node1: MOUNTED
        # node2: NOT_MOUNTED
        current_node = None
        for line in result.stdout.splitlines():
            # Match pattern "node: output"
            match = re.match(r'^([^:]+):\s*(.*)$', line)
            if match:
                node = match.group(1).strip()
                output = match.group(2).strip()
                
                if output == "MOUNTED":
                    mount_status[node] = True
                    messages[node] = "✓ Mounted"
                elif output == "NOT_MOUNTED":
                    mount_status[node] = False
                    messages[node] = "✗ Not mounted"
                else:
                    mount_status[node] = False
                    messages[node] = f"✗ Unexpected output: {output}"
        
        # Check for nodes that didn't respond
        for node in nodes:
            if node not in mount_status:
                mount_status[node] = False
                messages[node] = "✗ No response from node"
        
        # Check stderr for connection errors
        if result.stderr:
            for line in result.stderr.splitlines():
                # Parse clush error messages
                match = re.match(r'^clush: ([^:]+):', line)
                if match:
                    node = match.group(1).strip()
                    if node in nodes and node not in mount_status:
                        mount_status[node] = False
                        messages[node] = "✗ Connection failed"
                        
    except subprocess.TimeoutExpired:
        for node in nodes:
            mount_status[node] = False
            messages[node] = "✗ Command timeout"
    except FileNotFoundError:
        print("Error: 'clush' command not found. Please install clustershell package.")
        print("  Ubuntu/Debian: sudo apt install clustershell")
        print("  RHEL/CentOS: sudo yum install clustershell")
        print("  pip: pip install clustershell")
        sys.exit(1)
    except Exception as e:
        for node in nodes:
            mount_status[node] = False
            messages[node] = f"✗ Error: {str(e)}"
    
    return mount_status, messages


def check_all_nodes(nodes: list, mount_point: str):
    """
    Check mount status across all nodes using clush.
    """
    print(f"Checking mount point: {mount_point}")
    print(f"Nodes to check: {len(nodes)}")
    print(f"Using clush for parallel execution")
    print("-" * 60)
    
    mount_status, messages = check_mount_with_clush(nodes, mount_point)
    
    # Display results
    for node in nodes:
        status = messages.get(node, "✗ Unknown status")
        print(f"{node:30} {status}")
    
    print("-" * 60)
    
    # Summary
    mounted_count = sum(1 for is_mounted in mount_status.values() if is_mounted)
    total_count = len(nodes)
    
    print(f"\nSummary: {mounted_count}/{total_count} nodes have the mount point")
    
    if mounted_count == total_count:
        print("✓ All nodes are properly mounted")
        return 0
    else:
        print("✗ Some nodes are missing the mount")
        # List failed nodes
        failed_nodes = [node for node, is_mounted in mount_status.items() if not is_mounted]
        if failed_nodes:
            print(f"Failed nodes: {', '.join(failed_nodes)}")
        return 1


if __name__ == "__main__":
    # Parse command line arguments if provided
    import argparse
    
    parser = argparse.ArgumentParser(description="Check if a folder is mounted across multiple nodes using clush")
    parser.add_argument("-m", "--mount", default=MOUNT_POINT, help="Mount point to check")
    parser.add_argument("-u", "--user", default=SSH_USER, help="SSH username")
    parser.add_argument("-n", "--nodes", nargs="+", help="List of nodes to check")
    parser.add_argument("-g", "--group", help="Node group to check (uses clush -g option)")
    
    args = parser.parse_args()
    
    # Handle node group option
    if args.group:
        # Use clush group directly
        cmd = ["clush", "-g", args.group, "-N"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            nodes_to_check = result.stdout.strip().split()
        except Exception as e:
            print(f"Error: Could not expand group '{args.group}': {e}")
            sys.exit(1)
    else:
        nodes_to_check = args.nodes if args.nodes else NODES
    
    if not nodes_to_check:
        print("Error: No nodes specified. Edit NODES in the script, use -n flag, or use -g for a group.")
        sys.exit(1)
    
    exit_code = check_all_nodes(nodes_to_check, args.mount)
    sys.exit(exit_code)