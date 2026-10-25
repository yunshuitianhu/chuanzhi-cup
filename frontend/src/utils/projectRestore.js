/** @type {import('../types/contracts').GeometryKey[]} */
export const GEOMETRY_KEYS = ['river_name','length_m','width_avg_m','depth_avg_m','longitudinal_slope',
  'bed_elevation_avg_m','mannings_n','ammonia_nh3_mgL','dissolved_oxygen_mgL',
  'bod_cbod_mgL','water_temp_C','k_nitrification','k_cbod_decay','k_sediment_oxygen_demand','do_saturation_mgL']

/** @type {('load_kg'|'chainage_m'|'pollutant_type'|'source_start_h'|'duration_min')[]} */
export const POLLUTION_KEYS = ['load_kg','chainage_m','pollutant_type','source_start_h','duration_min']

/** @param {Pick<import('../types/contracts').ProjectState,'model'|'river_ui'>} saved
 * @param {Partial<import('../types/contracts').RiverForm>|null} draft
 * @param {import('../types/contracts').RiverForm} defaults
 */
export function restoreRiverWorkspace(saved, draft, defaults) {
  const model = saved.model, ui = saved.river_ui || {}
  const form = { ...defaults }
  if (model) {
    Object.assign(form, Object.fromEntries(Object.entries(model.config).filter(([,v]) => v != null)
      .filter(([key]) => Object.hasOwn(form,key))))
    form.n_cross_sections = model.cross_section_count || form.n_cross_sections
    form.upstream_flow_m3s = model.hydro_params.upstream_flow_m3s ?? model.config.avg_discharge_m3s
    form.high_water_mean_m = model.hydro_params.downstream_stage_m ?? form.high_water_mean_m
    form.duration_h = model.hydro_params.duration_h ?? form.duration_h
    const p = model.wq_params
    // 旧结果使用原水动力网格；不能恢复成新默认4并暗示它已加密计算。
    if (model.has_wq) form.wq_refinement = p.wq_refinement ?? 1
    const {load_kg,source_start_h,duration_min,chainage_m,pollutant_type}=p
    Object.assign(form, Object.fromEntries(Object.entries({load_kg,source_start_h,duration_min,chainage_m,pollutant_type})
      .filter(([,v]) => v != null)))
  }
  Object.assign(form, draft || {})
  const geometryMatches = !!model && model.initialized && GEOMETRY_KEYS.every(k => form[k] === model.config[k])
    && form.n_cross_sections === model.cross_section_count
  const hydroMatches = !!model && geometryMatches && model.has_hydro
    && form.upstream_flow_m3s === model.hydro_params.upstream_flow_m3s
    && form.high_water_mean_m === model.hydro_params.downstream_stage_m
    && form.duration_h === model.hydro_params.duration_h
    && form.tidal_amplitude_m === (model.hydro_params.tidal_amplitude_m ?? 0)
  return { form,
    init: model && geometryMatches ? { 断面数:model.cross_section_count, 河段长度_km:model.config.length_m/1000,
      曼宁糙率:model.config.mannings_n, ...ui.init } : null,
    hydro: hydroMatches ? { 工况:'恒定流', 结果可信度:'已恢复恒定流情景；请核实断面、边界和反应参数，结果不代表现场率定。', ...ui.hydro } : null,
    pollution: model && hydroMatches && model.has_wq
      && POLLUTION_KEYS.every(k => form[k] === model.wq_params[k])
      && form.duration_h === (model.wq_params.simulation_hours ?? model.hydro_params.duration_h)
      && form.wq_refinement === (model.wq_params.wq_refinement ?? 1)
      ? (ui.pollution || { 状态:'已恢复水质结果' }) : null }
}
