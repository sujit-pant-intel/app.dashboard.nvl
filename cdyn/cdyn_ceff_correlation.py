#!/usr/bin/env python3
"""
cdyn_ceff_correlation.py
=========================
Correlate SORT dynamic capacitance (CDYN, per-die) against E-TEST device Ceff
(RA4u), using IDW interpolation of the 9 e-test sites onto every die.

INPUTS (place in same folder, or pass paths):
  1. sort CSV            (e.g. 0.csv)      -- per-die CDYN + SORT_X/SORT_Y
  2. reticle map CSV     (8PL7CV-...-Reticle_Mapping.csv) -- DieX/DieY <-> ReticleShot
  3. e-test values       -- edit ETEST dict below (RA4u Isat N/P + Td per site),
                            OR point to a full e-test CSV via --etest

USAGE:
  python cdyn_ceff_correlation.py --sort 0.csv --reticle 8PL7CV-...csv
  (optional)  --power 2   --vdd 0.75   --etest etest_full.csv

OUTPUT:
  cdyn_ceff_perdie.csv           joined per-die table (CDYN + interpolated Ceff/UPM)
  cdyn_vs_ceff_scatter.png       correlation scatter (r, slope, intercept)
  cdyn_ceff_wafermaps.png        side-by-side wafer overlays
"""
import argparse, sys, re
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

# ---------------------------------------------------------------------------
# 9 e-test sites, RA4u:  Map -> (Isat_RNA4u, Isat_RPA4u, Td_RA4u[ps])
# EDIT these to your wafer, or use --etest to load a full e-test CSV instead.
# NOTE: Isat data stored in mA/um (= spec uA/um / 1000).  Ceff -> fF/um.
# ---------------------------------------------------------------------------
ETEST = {
 "SR_X-1Y-3":(5.38570,-5.77324,6.55497),
 "SR_X-2Y1": (5.39470,-5.71372,6.54668),
 "SR_X-5Y0": (5.52833,-5.76462,6.72560),
 "SR_X0Y-5": (5.59334,-5.82530,6.54399),
 "SR_X0Y0":  (4.85104,-5.57732,6.98958),
 "SR_X0Y5":  (5.30107,-5.78232,6.56350),
 "SR_X2Y-1": (5.64255,-5.86039,6.65237),
 "SR_X2Y3":  (5.08832,-5.33896,6.52922),
 "SR_X5Y0":  (5.41947,-5.92105,6.62393),
}

def find_col(df, *needles):
    """Return first column whose name contains all needles (case-insensitive)."""
    for c in df.columns:
        lc = c.lower()
        if all(n.lower() in lc for n in needles):
            return c
    return None

def load_reticle(path):
    r = pd.read_csv(path)
    r.columns = [c.strip() for c in r.columns]
    r['ReticleShot'] = r['ReticleShot'].astype(str).str.replace('\\','',regex=False).str.strip()
    return r[['DieX','DieY','LayoutX','LayoutY','ReticleShot']].copy()

def site_anchors(reticle, vdd):
    rows=[]
    for m,(n,p,td) in ETEST.items():
        sub = reticle[reticle.ReticleShot==m]
        if len(sub)==0:
            print(f"  WARN: e-test site {m} not in reticle map"); continue
        ieff=(abs(n)+abs(p))/2.0
        rows.append(dict(Map=m, cx=sub.DieX.mean(), cy=sub.DieY.mean(),
                         Ieff=ieff, Td=td, Ceff=ieff*td/vdd, UPM=500.0/td))
    return pd.DataFrame(rows)

def idw(px,py,sx,sy,sv,power=2.0):
    out=np.empty(len(px))
    for i in range(len(px)):
        d=np.sqrt((px[i]-sx)**2+(py[i]-sy)**2)
        hit=np.where(d<1e-9)[0]
        out[i]=sv[hit[0]] if len(hit) else np.sum((1/d**power)*sv)/np.sum(1/d**power)
    return out

def align_coords(sort_df, reticle, sx_col, sy_col):
    """Auto-detect integer offset mapping SORT_X/Y -> DieX/DieY by matching ranges."""
    sx_mid = (sort_df[sx_col].min()+sort_df[sx_col].max())/2
    sy_mid = (sort_df[sy_col].min()+sort_df[sy_col].max())/2
    dx_mid = (reticle.DieX.min()+reticle.DieX.max())/2
    dy_mid = (reticle.DieY.min()+reticle.DieY.max())/2
    ox, oy = round(dx_mid - sx_mid), round(dy_mid - sy_mid)
    print(f"  Coord auto-align: DieX = SORT_X + {ox},  DieY = SORT_Y + {oy}")
    return ox, oy

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--sort', required=True)
    ap.add_argument('--reticle', required=True)
    ap.add_argument('--power', type=float, default=2.0)
    ap.add_argument('--vdd', type=float, default=0.75)
    ap.add_argument('--offx', type=int, default=None, help='override DieX = SORT_X + offx')
    ap.add_argument('--offy', type=int, default=None)
    a=ap.parse_args()

    print("[1] Loading reticle map ..."); reticle=load_reticle(a.reticle)
    print("[2] Computing e-test site anchors ..."); site=site_anchors(reticle,a.vdd)
    print(site[['Map','cx','cy','Ceff','UPM']].round(3).to_string(index=False))

    print("[3] Loading sort CDYN ..."); s=pd.read_csv(a.sort)
    sx_col=find_col(s,'sort_x') or find_col(s,'sort','x')
    sy_col=find_col(s,'sort_y') or find_col(s,'sort','y')
    cd1=find_col(s,'cdyn','v1') ; cd2=find_col(s,'cdyn','v2')
    waf=find_col(s,'sort_wafer') or find_col(s,'wafer')
    if not all([sx_col,sy_col,cd1]):
        sys.exit(f"Could not find columns. Found X={sx_col} Y={sy_col} CDYN_V1={cd1}")
    for c in [cd1,cd2]:
        if c: s[c]=pd.to_numeric(s[c],errors='coerce')
    s=s.dropna(subset=[cd1]).copy()
    print(f"    dies with CDYN: {len(s)}   wafers: {s[waf].nunique() if waf else 'NA'}")

    # coordinate alignment
    if a.offx is not None and a.offy is not None:
        ox,oy=a.offx,a.offy; print(f"  Using manual offset ({ox},{oy})")
    else:
        ox,oy=align_coords(s,reticle,sx_col,sy_col)
    s['DieX']=s[sx_col]+ox ; s['DieY']=s[sy_col]+oy

    print(f"[4] IDW interpolation (power={a.power}) ...")
    px=s.DieX.values.astype(float); py=s.DieY.values.astype(float)
    s['Ceff']=idw(px,py,site.cx.values,site.cy.values,site.Ceff.values,a.power)
    s['UPM'] =idw(px,py,site.cx.values,site.cy.values,site.UPM.values, a.power)

    # ---- correlation ----
    print("[5] Correlation:")
    def corr(yv,label):
        m=~(np.isnan(s['Ceff'])|np.isnan(s[yv]))
        x=s['Ceff'][m]; y=s[yv][m]
        r,pv=stats.pearsonr(x,y); rho,_=stats.spearmanr(x,y)
        sl,ic=np.polyfit(x,y,1)
        print(f"    {label}: r={r:+.3f}  rho={rho:+.3f}  slope={sl:.4f}  intercept={ic:.3f}  n={m.sum()}")
        return r,rho,sl,ic
    r1=corr(cd1,'CDYN_V1 vs Ceff')
    if cd2: r2=corr(cd2,'CDYN_V2 vs Ceff')

    s.to_csv('cdyn_ceff_perdie.csv',index=False)

    # ---- scatter ----
    fig,ax=plt.subplots(1,2 if cd2 else 1,figsize=(13 if cd2 else 7,5.5),squeeze=False)
    for j,(cd,lab,rr) in enumerate([(cd1,'V1 (0.7V)',r1)]+([(cd2,'V2 (0.95V)',r2)] if cd2 else [])):
        A=ax[0][j]
        A.scatter(s['Ceff'],s[cd],s=8,alpha=.4,color='#1f77b4')
        xs=np.linspace(s['Ceff'].min(),s['Ceff'].max(),20)
        A.plot(xs,rr[2]*xs+rr[3],'r--',lw=2,label=f"r={rr[0]:+.3f}\nslope={rr[2]:.3f}")
        A.set_xlabel('Interpolated device Ceff RA4u (fF/um)')
        A.set_ylabel(f'Sort CDYN {lab} (nF)')
        A.set_title(f'CDYN {lab} vs device Ceff')
        A.grid(alpha=.3); A.legend()
    fig.suptitle('Sort CDYN vs E-test device Ceff (IDW power=%g)'%a.power,weight='bold')
    fig.tight_layout(); fig.savefig('cdyn_vs_ceff_scatter.png',dpi=150)

    # ---- wafer overlays ----
    fig,ax=plt.subplots(1,2,figsize=(15,6.5))
    for A,(col,title,cmap) in zip(ax,[('Ceff','Interpolated Ceff (fF/um)','viridis'),
                                      (cd1,'Sort CDYN_V1 (nF)','plasma')]):
        sc=A.scatter(s.DieX,s.DieY,c=s[col],s=25,cmap=cmap,marker='s')
        A.scatter(site.cx,site.cy,s=120,facecolors='none',edgecolors='red',lw=2)
        A.set_title(title); A.set_xlabel('DieX'); A.set_ylabel('DieY'); A.set_aspect('equal')
        plt.colorbar(sc,ax=A,shrink=.8)
    fig.suptitle('Wafer overlay: interpolated Ceff vs measured CDYN',weight='bold')
    fig.tight_layout(); fig.savefig('cdyn_ceff_wafermaps.png',dpi=150)

    print("\nDONE. Wrote: cdyn_ceff_perdie.csv, cdyn_vs_ceff_scatter.png, cdyn_ceff_wafermaps.png")
    print("Interpretation: |r| high & slope significant -> GT CDYN is DEVICE-limited (RA4u predicts it).")
    print("                |r| ~ 0 (flat)              -> GT CDYN is BEOL/wire-limited (RA4u won't match).")

if __name__=='__main__':
    main()
