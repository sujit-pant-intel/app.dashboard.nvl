I want to reuse information from yield-dashboard and etest-dashboard to build the parametric dashboard. It will run with yield dashboard but created a different html file and will have point in main dashboard.html.

This will be a differen tab in yield-dashboard window. Under Create , 
    a. Yield - current dashboard
    b. Parametric - includes everything from Yield plus additional info

    When Parametric is triggerd , it will generate 2 html
    a. Current Yield Dashboard html
    b. New Parametric dashboard html


    New Dashboard will 
    1. merge PCM data using either 9 point or full if available user option 
    2. it will merge reticle and material info
    3. Like etest-dashboard it will have tab for variability, distribution,XY and analysis  . All these will be bucketted under PCM analysis . You can put that in side bar
    4. Another Side bar will be Test Progra, it will have what is in upm/sicc/cdyn  
    5. Next Side bar is PCM-Program . right now it is blank . i will add to it once this is done 


    Create a different code folder called sort-parametric . this will ensure there is no interaction with yield dashboard . 




    PCM-PROGRAM Tracking 

    For PCM-PROGRAM Section , Page will be 3 panel 
    1st panel material, 2nd panel table / spec , 3r panel plot

    i will have these section 
    1. UPM vs Propagation Delay
        1st panel - lot/wafer/material filter like other pages 
        2nd panel - table of all the td_ (progation delay ) Param from PCM and UPM param from program(/test program)
        3rd panel - XY plot similra to to thta PCM Analysis but x will be UPM and Y will be td_4jru. provide option to configure x and y accodingly

    
    2. SICC vs Poff
        1st panel - lot/wafer/material filter like other pages 
        2nd panel - table of all the Poff Param from PCM and SICC param from program(/test program)
        (For SICC get spec for SDS , SDT from C:\scripts\app.yield.nvl\shared\spec\sicc-cdyn, use relative path to shared)
        3rd panel - XY plot similra to to thta PCM Analysis but x will be UPM and Y will be poff_4jru. provide option to configure x and y accodingly
