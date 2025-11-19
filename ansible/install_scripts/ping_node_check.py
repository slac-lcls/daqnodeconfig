#!/usr/bin/env python3
import subprocess
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed

def check_node(node):
    """
    Check if a node is reachable via ping.
    Returns tuple: (node, status, response_time)
    """
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, '1', '-W' if platform.system().lower() != 'windows' else '-w', '1000', node]
    
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=2)
        if output.returncode == 0:
            # Extract response time from ping output
            response_time = "< 1ms"
            for line in output.stdout.split('\n'):
                if 'time=' in line.lower():
                    response_time = line.split('time=')[1].split()[0]
                    break
            return (node, True, response_time)
        else:
            return (node, False, None)
    except (subprocess.TimeoutExpired, Exception) as e:
        return (node, False, None)

def check_nodes(nodes, max_workers=10):
    """
    Check multiple nodes concurrently.
    nodes: list of IP addresses or hostnames
    max_workers: number of concurrent checks
    """
    results = []
    
    if len(nodes) != 1:
        print(f"Checking {len(nodes)} nodes...\n")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_node, node): node for node in nodes}
        
        for future in as_completed(futures):
            node, status, response_time = future.result()
            results.append((node, status, response_time))
            space=''
            status_str = "✓ UP" if status else "✗ DOWN"
            time_str = f" ({response_time})" if response_time else ""
            if "ipmi" in node: space='----'
            if not status or "ipmi" in node: print(f"{space}{node:20} {status_str}{time_str}")
            if not status and "ipmi" not in node:
                result1 = check_nodes([f"{node}-ipmi.pcdsn"])    
                
    return results

def print_summary(results):
    """Print summary of node status checks."""
    up_nodes = [r for r in results if r[1]]
    down_nodes = [r for r in results if not r[1]]
    
    print(f"\n{'='*50}")
    print(f"Summary: {len(up_nodes)}/{len(results)} nodes are UP")
    print(f"{'='*50}")
    
    if down_nodes:
        print("\nDown nodes:")
        for node, _, _ in down_nodes:
            print(f"  - {node}")

if __name__ == "__main__":
    # Define your nodes here
    nodes=[]
    for i in range(1,57):
        nodes.append(f"drp-srcf-cmp{i:03d}")     
    for i in range(1,11):
        nodes.append(f"drp-srcf-mon{i:03d}")
    for i in range(1,6):
        nodes.append(f"drp-srcf-gpu{i:03d}")

    results = check_nodes(nodes)
    print_summary(results)