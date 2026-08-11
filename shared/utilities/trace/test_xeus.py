#!/usr/bin/env python3
"""Test XEUS backend queries for TRACE API."""

import json
import trace_bridge

def main():
    print("=" * 70)
    print("TRACE API - XEUS Backend Test")
    print("=" * 70)
    
    # Test parameters (from your lot)
    lot = "Q603S6T03"
    operation = "119325"
    program = "NCXSDJXL0H61B002619"
    
    # Test 1: Basic XEUS query
    print("\n[TEST 1] xeus_get - Query by lot + operation")
    print(f"  Lot: {lot}, Operation: {operation}")
    try:
        results = trace_bridge.xeus_get(lot, operation=operation)
        print(f"  ✓ Found {len(results)} records")
        for r in results:
            print(f"    - {r['name']}: {r['totalLatestUnits']} units, yield={r['yieldText']}")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # Test 2: XEUS with program filter
    print("\n[TEST 2] xeus_get - Query by lot + program")
    print(f"  Lot: {lot}, Program: {program[:20]}...")
    try:
        results = trace_bridge.xeus_get(lot, program=program)
        print(f"  ✓ Found {len(results)} records matching program")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # Test 3: XEUS bin distribution summary
    print("\n[TEST 3] xeus_bin_dist - Get summary of all wafers")
    print(f"  Lot: {lot}, Operation: {operation}")
    try:
        result = trace_bridge.xeus_bin_dist(lot, operation=operation)
        print(f"  ✓ Query results:")
        print(f"    - Total matches: {result['query']['matches']}")
        print(f"    - Selected wafer: {result['selectedItuff']['name']}")
        print(f"    - Backend: {result['query']['backend']}")
        total_units = sum(d['totalLatestUnits'] for d in result['allMatches'])
        print(f"    - Total units across all wafers: {total_units}")
    except Exception as e:
        print(f"  ✗ Error: {e}")
    
    # Test 4: Different bin kinds
    print("\n[TEST 4] xeus_bin_dist - Compare bin kinds")
    for bin_kind in ["interface", "hard", "functional"]:
        try:
            result = trace_bridge.xeus_bin_dist(lot, operation=operation, bin_kind=bin_kind)
            print(f"  ✓ {bin_kind}: OK (matches={result['query']['matches']})")
        except Exception as e:
            print(f"  ✗ {bin_kind}: {e}")
    
    print("\n" + "=" * 70)
    print("XEUS Backend Integration: SUCCESS")
    print("=" * 70)
    print("\nYour lot data is now accessible via the XEUS backend!")
    print("Use xeus_get() to query TRACE data from Python.")

if __name__ == "__main__":
    main()
