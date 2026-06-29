This script analyse the scan hry data . 

steps
1. Generate Scan HRY from Birta+ tool

2. Go to script area 
G:\users\tools\snpant\automation\fte_scripts\nvl\bin\scan\scripts

G drive mapped as \\samba.zsc10.intel.com\nfs\zsc10\disks\gsc_gwa011\

3. Run scripts 
	1. stack_level4_hry_scan.jsl                  - Run this on Brita generated hry file. IT will parse and generate parse level4 data table for further analysis
	2.1 YieldLoss Per Wafer TestType Module.jsl   - generate the fail information for each lot/wafer/session for each test type
	2.2 Plot YieldLoss Per Module.jsl             - plot yield loss per module (CORE0,CORE1.., ATOM0, ATOM1 ... , UNCORE)
	2.3 Plot YieldLoss Per Block.jsl              - plot yield loss per block (CORE, ATOM, UNCORE)
	

