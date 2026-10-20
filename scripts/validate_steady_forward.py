"""恒定流正演离线核验：解析解、独立ODE、收敛与条件参数敏感性；不是现场率定。"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from scipy.integrate import solve_ivp
from scipy.special import erf
from river_model.config import RiverConfig
from river_model.state import HydroResult
from river_model.transport import geometry, conservative_step
from river_model.wq_engine import solve_water_quality
from river_model.cross_sections import generate_cross_sections
from river_model.hydro_engine import run_hydrodynamic

SOURCES = {
    'verification': 'https://www.grc.nasa.gov/www/wind/valid/tutorial/verassess.html',
    'convergence': 'https://www.grc.nasa.gov/www/wind/valid/tutorial/spatconv.html',
    'river_calibration_limits': 'https://water.usgs.gov/osw/pubs/disp/dispersion.html',
}
# 预先声明的工程验收门限，只约束这里的受控算例。
LIMITS = {'finest_relative_l1': .06, 'mass_relative_residual': 1e-10,
          'hydro_max_stage_error_m': .001, 'reaction_max_error_mgL': 1e-7}


def dual_widths(x):
    """独立于transport.cell_widths的几何积分权重。"""
    edges = np.r_[x[0], (x[:-1]+x[1:])/2, x[-1]]
    return edges, np.diff(edges)


def uniform_hydro(x, width, depth, speed):
    area = np.full((1,len(x)),width*depth)
    return HydroResult(t=np.array([0.]),chainage=x,water_level=np.full_like(area,depth),
        area=area,velocity=np.full_like(area,speed),discharge=area*speed,
        params={'engine':'independent_uniform_steady_fixture','tidal_amplitude_m':0.})


def gaussian_cell_average(x, center, variance, area=40., mass_g=1000.):
    edges, widths=dual_widths(x)
    cdf=.5*(1+erf((edges-center)/np.sqrt(2*variance)))
    return mass_g/area*np.diff(cdf)/widths


def l1_relative(actual, exact, weights):
    return float(np.sum(np.abs(actual-exact)*weights)/np.sum(np.abs(exact)*weights))


def orders(errors, ratio=2.):
    return [float(np.log(a/b)/np.log(ratio)) if min(a,b)>1e-14 else None
            for a,b in zip(errors[:-1],errors[1:])]


def march(x, initial, speed, diffusion, dt, seconds, area=40.):
    volume,q,conductance,rate=geometry(x,np.full(len(x),area),
        np.full(len(x),area*speed),np.full(len(x),diffusion))
    if rate*dt> .9+1e-12:
        raise ValueError('验证步长超出稳定区；不得把自动缩步混作独立时间收敛')
    c=initial[None,:].copy()
    incoming=outgoing=0.
    steps=int(np.ceil(seconds/dt))
    for k in range(steps):
        h=min(dt,seconds-k*dt)
        c,entry,exit_=conservative_step(c,volume,q,conductance,h,np.zeros(1),np.zeros(1))
        incoming+=float(entry[0]);outgoing+=float(exit_[0])
    _,widths=dual_widths(x)
    initial_mass=float(np.dot(initial,widths*area))
    residual=float(np.dot(c[0],widths*area))-initial_mass-incoming+outgoing
    return c[0], {'relative_mass_residual':abs(residual)/max(initial_mass,1.),
        'outflow_g':outgoing,'minimum_concentration':float(c.min()),'dt_s':dt,
        'max_stability':rate*dt,'steps':steps}


def transport_benchmarks():
    families=[]; profiles=[]
    for speed in (.35,-.35,0.):
        rows=[]
        for nodes in (81,161,321):
            x=np.linspace(-1000,1000,nodes); _,widths=dual_widths(x)
            initial=gaussian_cell_average(x,-200.,60.**2)
            exact=gaussian_cell_average(x,-200.+speed*240.,60.**2+2*6.*240.)
            actual,info=march(x,initial,speed,6.,.05,240.)
            row={'nodes':nodes,'dx_m':float(x[1]-x[0]),
                'relative_l1':l1_relative(actual,exact,widths),**info}
            rows.append(row)
            if speed==.35: profiles.append((x,actual,exact))
        errors=[r['relative_l1'] for r in rows]
        families.append({'velocity_ms':speed,'dispersion_m2s':6.,'time_s':240.,
            'reference':'无限域高斯平移扩散解，按每个节点控制体积积分；边界在云团远场',
            'rows':rows,'observed_orders':orders(errors),
            'checks':{'decreasing_error':all(a>b for a,b in zip(errors[:-1],errors[1:])),
                'finest_under_limit':errors[-1]<LIMITS['finest_relative_l1'],
                'mass_and_positivity':all(r['relative_mass_residual']<LIMITS['mass_relative_residual']
                    and r['minimum_concentration']>=0 for r in rows)}})
    # 固定空间算子，对比其精确矩阵模态，而不是把空间误差混入时间阶数。
    x=np.linspace(0,200,41);dx=x[1]-x[0];mode=5;diffusion=2.;seconds=64.
    cosine=np.cos(mode*np.pi*x/200.)
    eigenvalue=-4*diffusion*np.sin(mode*np.pi/(2*(len(x)-1)))**2/dx**2
    exact=2.+cosine*np.exp(eigenvalue*seconds)
    temporal=[]
    for dt in (4.,2.,1.):
        actual,info=march(x,2.+cosine,0.,diffusion,dt,seconds)
        temporal.append({'dt_s':dt,'relative_l1':l1_relative(actual,exact,np.ones(len(x))),**info})
    errors=[r['relative_l1'] for r in temporal]
    return {'spatial':families,'temporal':{'rows':temporal,'observed_orders':orders(errors),
        'reference':'固定网格Neumann扩散离散余弦特征模态的精确指数；仅隔离时间误差',
        'checks':{'decreasing_error':all(a>b for a,b in zip(errors[:-1],errors[1:])),
            'first_order_range':all(.8<p<1.2 for p in orders(errors))}}}, profiles


def source_reference(x, area, speed, dispersion, time_s, source_m, mass_kg, start_s, end_s):
    """有限时段点源Green函数卷积，Gauss-Legendre积分，空间也按控制体积积分。"""
    if not (0<=start_s<end_s<time_s): raise ValueError('参照要求排放已结束')
    nodes,weights=np.polynomial.legendre.leggauss(64)
    release=(end_s+start_s)/2+(end_s-start_s)/2*nodes
    ages=time_s-release
    edges,widths=dual_widths(x)
    scaled=(edges[:,None]-source_m-speed*ages)/np.sqrt(4*dispersion*ages)
    kernel_integral=.5*np.diff(erf(scaled),axis=0)/widths[:,None]
    return mass_kg*1000/(area*2)*np.dot(kernel_integral,weights)


def full_solver_benchmark(length=6000.,width=30.,depth=2.,speed=.2,time_s=600.,start_s=37.,duration_s=60.):
    cfg=RiverConfig(width_avg_m=width,depth_avg_m=depth,depth_range_m=(depth,depth),
        mannings_n=.028,ammonia_nh3_mgL=0.,bod_cbod_mgL=0.,k_nitrification=0.,
        k_sediment_oxygen_demand=0.,k_reaeration_base=0.,dissolved_oxygen_mgL=8.)
    area=width*depth;end_s=start_s+duration_s;source_m=length/3
    # 独立写出本算例经验闭合，不调用被测_fischer_dispersion。
    shear=(9.81*depth*(.028*speed/depth**(2/3))**2)**.5
    diffusion=float(np.clip(.011*speed**2*width**2/(depth*shear),.5,5000.))
    rows=[];profile=None
    for nodes in (151,301,601):
        x=np.linspace(0,length,nodes)
        result=solve_water_quality(cfg,uniform_hydro(x,width,depth,speed),load_kg=1.,chainage_m=source_m,
            duration_min=duration_s/60,source_start_h=start_s/3600,simulation_hours=time_s/3600,
            dt_s=1.,output_times_h=np.array([time_s/3600]))
        exact=source_reference(x,area,speed,diffusion,time_s,source_m,1.,start_s,end_s)
        _,widths=dual_widths(x)
        independent_mass_kg=float(np.dot(result.ammonia[-1],widths*area)/1000)
        rows.append({'nodes':nodes,'relative_l1':l1_relative(result.ammonia[-1],exact,widths),
            'peak_relative_error':float(abs(result.ammonia[-1].max()-exact.max())/exact.max()),
            'independent_final_mass_kg':independent_mass_kg,
            'source_snap_m':result.params['source_snap_distance_m'],
            'numerics':result.params.get('numerical_diagnostics')})
        profile=(x,result.ammonia[-1],exact)
    errors=[r['relative_l1'] for r in rows]
    return {'rows':rows,'dispersion_m2s':diffusion,
        'river':{'length_m':length,'width_m':width,'depth_m':depth,'velocity_ms':speed},
        'reference':'常系数无限域有限时段点源解析Green函数卷积；k_n=0',
        'checks':{'decreasing_error':all(a>b for a,b in zip(errors[:-1],errors[1:])),
            'finest_under_limit':errors[-1]<LIMITS['finest_relative_l1'],
            'mass_integral':all(abs(r['independent_final_mass_kg']-1.)<1e-9 for r in rows)}},profile


def reaction_benchmark():
    cfg=RiverConfig(width_avg_m=20.,depth_avg_m=2.,depth_range_m=(2.,2.),
        water_temp_C=20.,ammonia_nh3_mgL=.8,bod_cbod_mgL=2.,dissolved_oxygen_mgL=4.,
        k_nitrification=.15,k_cbod_decay=.25,k_sediment_oxygen_demand=.3,k_reaeration_base=0.)
    # 独立实现科学方程并采用隐式Radau，不调用被测反应RHS或质量账本。
    def rhs(t,y):
        carbon,nitrogen,oxygen=y
        oxidation=.25*carbon*oxygen/(.5+oxygen)
        nitrification=.15*nitrogen*oxygen/(.6+oxygen)
        return np.array([-oxidation-.05*carbon,-nitrification,
            -oxidation-4.57*nitrification-.3/2*oxygen/(.5+oxygen)])/86400.
    reference=solve_ivp(rhs,(0.,86400.),[2.,.8,4.],method='Radau',rtol=2e-12,atol=2e-13)
    if not reference.success: raise RuntimeError('独立Radau未收敛')
    rows=[]
    for dt in (3600.,1800.,900.):
        # 均匀场无需空间加密；取宽单元，使最小弥散系数不把三个请求步长
        # 都自动缩成同一子步。实际时间步须与请求步长一致才可估计反应阶数。
        x=np.linspace(0,2000,3)
        result=solve_water_quality(cfg,uniform_hydro(x,20.,2.,0.),point_sources=[],
            simulation_hours=24.,dt_s=dt,output_times_h=np.array([24.]))
        actual=np.array([result.cbod[-1,1],result.ammonia[-1,1],result.dissolved_oxygen[-1,1]])
        rows.append({'dt_s':dt,'max_error_mgL':float(abs(actual-reference.y[:,-1]).max()),
            'actual':actual.tolist(),'numerics':result.params.get('numerical_diagnostics')})
    errors=[r['max_error_mgL'] for r in rows]
    return {'rows':rows,'reference':reference.y[:,-1].tolist(),'observed_orders':orders(errors),
        'checks':{'decreasing_error':all(a>b for a,b in zip(errors[:-1],errors[1:])),
            'actual_time_steps_differ':all(abs(r['numerics']['actual_max_dt_s']-r['dt_s'])<1e-9
                and abs(r['numerics']['actual_min_dt_s']-r['dt_s'])<1e-9 for r in rows),
            'finest_under_limit':errors[-1]<LIMITS['reaction_max_error_mgL']}}


def hydro_benchmark():
    width,side,roughness,depth,slope,length=20.,1.5,.03,2.,.00004,2400.
    def conveyance(h):
        area=width*h+side*h*h
        return area*(area/(width+2*h*np.sqrt(1+side*side)))**(2/3)/roughness
    q=conveyance(depth)*np.sqrt(slope)
    normal=[];backwater=[]
    for nodes in (21,41,81):
        cfg=RiverConfig(length_m=length,width_avg_m=26.,depth_avg_m=2.,depth_range_m=(1.,4.))
        sections=generate_cross_sections(nodes,length,0.,slope,width,6.,side,roughness)
        bed=lambda x:-slope*(np.asarray(x)-length/2)
        for mode,downstream_depth in (('normal',depth),('backwater',2.8)):
            hydro=run_hydrodynamic(cfg,sections,upstream_flow_m3s=float(q),
                downstream_stage_m=float(bed(length)+downstream_depth),duration_h=.01,dt_s=36.)
            if mode=='normal': exact=bed(hydro.chainage)+depth
            else:
                # 只核验实现中的忽略对流加速度模型；这不是完整Saint-Venant独立验收。
                def rhs(x,z): return [-(q/conveyance(z[0]-bed(x)))**2]
                ode=solve_ivp(rhs,(length,0.),[float(bed(length)+2.8)],method='DOP853',
                    rtol=1e-12,atol=1e-13,dense_output=True)
                if not ode.success:raise RuntimeError('回水参照ODE未收敛')
                exact=ode.sol(hydro.chainage)[0]
            error=float(abs(hydro.water_level[-1]-exact).max())
            row={'nodes':nodes,'max_stage_error_m':error,
                'max_relative_flow_error':float(abs(hydro.discharge-q).max()/q)}
            (normal if mode=='normal' else backwater).append(row)
    return {'normal_manning':normal,'local_inertia_backwater':backwater,
        'reference':'独立梯形断面Manning均匀流；忽略对流加速度的回水ODE由DOP853积分',
        'limitations':'仅低Froude单向恒定流；不核验急流、水跃、潮汐或完整Saint-Venant动量方程',
        'checks':{'stage_within_one_mm':all(r['max_stage_error_m']<LIMITS['hydro_max_stage_error_m']
                for r in normal+backwater),
            'steady_flow_preserved':all(r['max_relative_flow_error']<1e-8 for r in normal+backwater)}}


def sensitivity_study():
    cfg=RiverConfig(width_avg_m=30.,depth_avg_m=2.,depth_range_m=(2.,2.),
        mannings_n=.028,ammonia_nh3_mgL=.3,bod_cbod_mgL=3.,dissolved_oxygen_mgL=6.,
        k_nitrification=.15,k_cbod_decay=.25,k_sediment_oxygen_demand=.5,
        water_temp_C=25.,k_reaeration_base=5.32)
    base_flow=15.;base_load=5.
    def calculate(config=cfg,flow=base_flow,load=base_load,nodes=121):
        x=np.linspace(0,2400,nodes); area=config.width_avg_m*config.depth_avg_m
        h=uniform_hydro(x,config.width_avg_m,config.depth_avg_m,flow/area)
        outputs=np.arange(0,7200+1,60.)/3600
        common=dict(chainage_m=600.,source_start_h=.1,duration_min=10.,simulation_hours=2.,
            dt_s=15.,output_times_h=outputs)
        active=solve_water_quality(config,h,load_kg=load,**common)
        background=solve_water_quality(config,h,load_kg=0.,**common)
        probe=int(np.argmin(abs(x-1200.)))
        excess=active.ammonia[:,probe]-background.ammonia[:,probe]
        peak=int(np.argmax(excess))
        return {'peak_excess_nh3_mgL':float(excess[peak]),'peak_time_h':float(active.t[peak]),
            'final_probe_do_mgL':float(active.dissolved_oxygen[-1,probe]),
            'nitrified_n_kg':active.params['reaction_diagnostics']['nitrified_n_kg']}
    base=calculate();fine=calculate(nodes=241)
    grid_effect=abs(fine['peak_excess_nh3_mgL']-base['peak_excess_nh3_mgL'])/base['peak_excess_nh3_mgL']
    rows=[]
    for parameter in ('load_kg','flow_m3s','width_avg_m','depth_avg_m','mannings_n',
            'k_nitrification','k_cbod_decay','k_sediment_oxygen_demand','k_reaeration_base'):
        pair=[]
        for factor in (.8,1.2):
            config=cfg;flow=base_flow;load=base_load
            if parameter=='load_kg':load*=factor
            elif parameter=='flow_m3s':flow*=factor
            else:
                config=replace(cfg,**{parameter:getattr(cfg,parameter)*factor})
                if parameter=='depth_avg_m':config.depth_range_m=(config.depth_avg_m,config.depth_avg_m)
            pair.append(calculate(config,flow,load))
        response=(pair[1]['peak_excess_nh3_mgL']-pair[0]['peak_excess_nh3_mgL'])/base['peak_excess_nh3_mgL']
        rows.append({'parameter':parameter,'minus20':pair[0],'plus20':pair[1],
            'peak_normalized_central_sensitivity':float(response/.4),
            'peak_response_below_grid_difference':bool(abs(response)<grid_effect)})
    by_name={r['parameter']:r for r in rows}
    return {'baseline':base,'finer_grid':fine,'peak_grid_relative_difference':float(grid_effect),
        'rows':rows,'scope':'单因素±20%，外给恒定流量、矩形面积与u=Q/A保持一致；糙率只作用于给定水动力上的经验弥散闭合。非联合敏感性或后验区间。',
        'checks':{'load_increases_peak':by_name['load_kg']['plus20']['peak_excess_nh3_mgL']>
                by_name['load_kg']['minus20']['peak_excess_nh3_mgL'],
            'reaeration_changes_do':by_name['k_reaeration_base']['plus20']['final_probe_do_mgL']>
                by_name['k_reaeration_base']['minus20']['final_probe_do_mgL']}}


def plot_results(out,report,profiles,source_profile):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(15,4.3),layout='constrained')
    for i,(x,actual,exact) in enumerate(profiles):
        axes[0].plot(x,actual,label=f'{len(x)} nodes')
    axes[0].plot(profiles[-1][0],profiles[-1][2],'k--',label='Analytic cell average')
    axes[0].set(xlim=(-450,200),xlabel='Distance (m)',ylabel='Concentration (mg/L)',title='Advection + dispersion')
    axes[0].legend(fontsize=8)
    for row in report['transport']['spatial']:
        axes[1].loglog([r['dx_m'] for r in row['rows']],[r['relative_l1'] for r in row['rows']],
            'o-',label=f"u={row['velocity_ms']} m/s")
    axes[1].set(xlabel='Grid spacing (m)',ylabel='Relative L1 error',title='Spatial convergence')
    axes[1].legend(fontsize=8);axes[1].grid(True,which='both',alpha=.25)
    temporal=report['transport']['temporal']['rows']
    axes[2].loglog([r['dt_s'] for r in temporal],[r['relative_l1'] for r in temporal],'o-')
    axes[2].set(xlabel='Time step (s)',ylabel='Temporal relative L1 error',title='Fixed-space temporal convergence')
    axes[2].grid(True,which='both',alpha=.25)
    fig.savefig(out/'transport_convergence.png',dpi=180);plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4),layout='constrained')
    x,actual,exact=source_profile
    ax.plot(x,exact,'k--',label='Finite-source Green function convolution')
    ax.plot(x,actual,label='Full ADR solver, 601 nodes')
    ax.set(xlim=(1700,2500),xlabel='Distance (m)',ylabel='NH3 (mg/L)',title='Independent full-solver reference (6 km controlled case)')
    ax.legend(fontsize=8);fig.savefig(out/'finite_source_profile.png',dpi=180);plt.close(fig)
    if report.get('sensitivity'):
        rows=sorted(report['sensitivity']['rows'],key=lambda r:abs(r['peak_normalized_central_sensitivity']))
        fig,ax=plt.subplots(figsize=(9,5.5))
        colors=['#e09f3e' if r['peak_response_below_grid_difference'] else '#2374ab' for r in rows]
        ax.barh([r['parameter'] for r in rows],[r['peak_normalized_central_sensitivity'] for r in rows],color=colors)
        ax.axvline(0,color='black',linewidth=.7)
        ax.set(xlabel='Normalized central sensitivity of peak excess NH3',title='Conditional sensitivity (each input +/-20%)')
        fig.tight_layout(rect=(0,.06,1,1))
        fig.text(.5,.015,'Orange: response smaller than the 121 -> 241 node grid difference',ha='center',fontsize=8)
        fig.savefig(out/'conditional_sensitivity.png',dpi=180);plt.close(fig)


def run(out,skip_sensitivity=False,no_plots=False):
    import scipy
    out.mkdir(parents=True,exist_ok=True)
    print('开始独立输移解析解与时间收敛验证',flush=True)
    transport,profiles=transport_benchmarks()
    print('开始端到端点源卷积、独立反应ODE及水动力参照',flush=True)
    full,source_profile=full_solver_benchmark()
    additional=[full_solver_benchmark(3000.,15.,1.,.15,300.,11.,30.)[0],
                full_solver_benchmark(12000.,60.,2.5,.3,1200.,73.,120.)[0]]
    reaction=reaction_benchmark();hydro=hydro_benchmark()
    print('开始参数敏感性和网格误差对照',flush=True)
    sensitivity=None if skip_sensitivity else sensitivity_study()
    report={'scope':'数值核验与条件敏感性，不是现场率定、真实准确率或反演验收',
        'versions':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__},
        'acceptance_limits':LIMITS,'sources':SOURCES,'transport':transport,'full_solver':full,
        'additional_river_cases':additional,
        'reaction':reaction,'hydrodynamics':hydro,'sensitivity':sensitivity,
        'limits':['解析解分别检验受控子问题；没有独立野外示踪数据',
            '敏感性小于网格差异的参数不可据此宣称已被观测识别',
            '当前迎风格式有数值弥散；水动力忽略对流加速度，不适用于水跃/急流/潮汐']}
    checks=[r['checks'] for r in transport['spatial']]+[transport['temporal']['checks'],full['checks'],reaction['checks'],hydro['checks']]
    checks.extend(r['checks'] for r in additional)
    if sensitivity:checks.append(sensitivity['checks'])
    report['passed']=all(all(check.values()) for check in checks)
    (out/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    if not no_plots:plot_results(out,report,profiles,source_profile)
    print(json.dumps({'passed':report['passed'],'checks':checks},ensure_ascii=False),flush=True)
    return report['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'work/steady-forward-validation')
    parser.add_argument('--skip-sensitivity',action='store_true')
    parser.add_argument('--no-plots',action='store_true')
    args=parser.parse_args()
    raise SystemExit(0 if run(args.output_dir,args.skip_sensitivity,args.no_plots) else 1)
