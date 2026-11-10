"""Convert a PrusaSlicer printer preset (.ini, from File > Export > Export Config) into an OrcaSlicer printer
preset (JSON) that builds on one of OrcaSlicer's own printers.

OrcaSlicer descends from PrusaSlicer, so most printer settings carry over under a new name; G-code macros mostly
keep working once a handful of placeholders are renamed. What can't be carried over is listed so a person can
check it. Print and filament settings in the same file are not part of a printer preset and are left out.
"""
import json
import re

# PrusaSlicer printer key -> OrcaSlicer machine key
KEY_MAP = {
    "bed_shape": "printable_area",
    "max_print_height": "printable_height",
    "z_offset": "z_offset",
    "nozzle_diameter": "nozzle_diameter",
    "extruder_offset": "extruder_offset",
    "extruder_colour": "extruder_colour",
    "retract_length": "retraction_length",
    "retract_lift": "z_hop",
    "retract_lift_above": "retract_lift_above",
    "retract_lift_below": "retract_lift_below",
    "retract_speed": "retraction_speed",
    "deretract_speed": "deretraction_speed",
    "retract_restart_extra": "retract_restart_extra",
    "retract_before_travel": "retraction_minimum_travel",
    "retract_before_wipe": "retract_before_wipe",
    "retract_layer_change": "retract_when_changing_layer",
    "retract_length_toolchange": "retract_length_toolchange",
    "retract_restart_extra_toolchange": "retract_restart_extra_toolchange",
    "wipe": "wipe",
    "min_layer_height": "min_layer_height",
    "max_layer_height": "max_layer_height",
    "start_gcode": "machine_start_gcode",
    "end_gcode": "machine_end_gcode",
    "before_layer_gcode": "before_layer_change_gcode",
    "layer_gcode": "layer_change_gcode",
    "toolchange_gcode": "change_filament_gcode",
    "pause_print_gcode": "machine_pause_gcode",
    "template_custom_gcode": "template_custom_gcode",
    "gcode_flavor": "gcode_flavor",
    "use_relative_e_distances": "use_relative_e_distances",
    "use_firmware_retraction": "use_firmware_retraction",
    "silent_mode": "silent_mode",
    "single_extruder_multi_material": "single_extruder_multi_material",
    "cooling_tube_retraction": "cooling_tube_retraction",
    "cooling_tube_length": "cooling_tube_length",
    "parking_pos_retraction": "parking_pos_retraction",
    "extra_loading_move": "extra_loading_move",
    "high_current_on_filament_swap": "high_current_on_filament_swap",
    "printer_notes": "printer_notes",
    "thumbnails": "thumbnails",
    "machine_limits_usage": "emit_machine_limits_to_gcode",
}
# the machine limits keep their names
for _axis in ("x", "y", "z", "e"):
    KEY_MAP[f"machine_max_feedrate_{_axis}"] = f"machine_max_feedrate_{_axis}"
    KEY_MAP[f"machine_max_jerk_{_axis}"] = f"machine_max_jerk_{_axis}"
    KEY_MAP[f"machine_max_acceleration_{_axis}"] = f"machine_max_acceleration_{_axis}"
for _k in ("machine_max_acceleration_extruding", "machine_max_acceleration_retracting", "machine_max_acceleration_travel",
           "machine_min_extruding_rate", "machine_min_travel_rate"):
    KEY_MAP[_k] = _k

KEY_MAP.update({"between_objects_gcode": "printing_by_object_gcode", "extruder_clearance_radius": "extruder_clearance_radius",
                "extruder_clearance_height": "extruder_clearance_height_to_lid", "travel_slope": "travel_slope",
                "travel_ramping_lift": "travel_ramping_lift", "travel_max_lift": "travel_max_lift",
                "travel_lift_before_obstacle": "travel_lift_before_obstacle"})

# keys PrusaSlicer keeps in its printer presets (from its own vendor bundles); everything else in an export is
# a print or filament setting and is not part of a printer preset
PRUSA_PRINTER_KEYS = set("""autoemit_temperature_commands bed_shape before_layer_gcode between_objects_gcode binary_gcode
color_change_gcode cooling_tube_length cooling_tube_retraction default_filament_profile default_print_profile deretract_speed
end_gcode extra_loading_move extruder_clearance_height extruder_clearance_radius extruder_colour extruder_offset gcode_flavor
high_current_on_filament_swap host_type inherits layer_gcode machine_limits_usage machine_max_acceleration_e
machine_max_acceleration_extruding machine_max_acceleration_retracting machine_max_acceleration_travel
machine_max_acceleration_x machine_max_acceleration_y machine_max_acceleration_z machine_max_feedrate_e machine_max_feedrate_x
machine_max_feedrate_y machine_max_feedrate_z machine_max_jerk_e machine_max_jerk_x machine_max_jerk_y machine_max_jerk_z
machine_max_junction_deviation machine_min_extruding_rate machine_min_travel_rate max_layer_height max_print_height
min_layer_height multimaterial_purging nozzle_diameter nozzle_high_flow octoprint_apikey octoprint_host parking_pos_retraction
pause_print_gcode prefer_clockwise_movements printer_model printer_notes printer_settings_id printer_technology printer_variant
printer_vendor remaining_times renamed_from retract_before_travel retract_before_wipe retract_layer_change retract_length
retract_length_toolchange retract_lift retract_lift_above retract_lift_below retract_restart_extra
retract_restart_extra_toolchange retract_speed serial_port serial_speed silent_mode single_extruder_multi_material start_gcode
thumbnails thumbnails_format toolchange_gcode travel_lift_before_obstacle travel_max_lift travel_ramping_lift travel_slope
use_firmware_retraction use_relative_e_distances use_volumetric_e variable_layer_height wipe z_offset template_custom_gcode""".split())

# values that are one entry per tool (or per bed corner) in both slicers
VECTOR = {"printable_area", "nozzle_diameter", "extruder_offset", "extruder_colour", "retraction_length", "z_hop",
          "retract_lift_above", "retract_lift_below", "retraction_speed", "deretraction_speed", "retract_restart_extra",
          "retraction_minimum_travel", "retract_before_wipe", "retract_when_changing_layer", "retract_length_toolchange",
          "retract_restart_extra_toolchange", "wipe", "min_layer_height", "max_layer_height", "thumbnails",
          "travel_slope", "travel_ramping_lift", "travel_max_lift", "travel_lift_before_obstacle"}
VECTOR |= {k for k in KEY_MAP.values() if k.startswith(("machine_max_", "machine_min_"))}

GCODE = {"printing_by_object_gcode", "machine_start_gcode", "machine_end_gcode", "before_layer_change_gcode", "layer_change_gcode",
         "change_filament_gcode", "machine_pause_gcode", "template_custom_gcode"}

# PrusaSlicer placeholder -> OrcaSlicer placeholder
PLACEHOLDERS = {
    "first_layer_temperature": "nozzle_temperature_initial_layer",
    "temperature": "nozzle_temperature",
    "first_layer_bed_temperature": "bed_temperature_initial_layer_single",
    "first_layer_height": "initial_layer_print_height",
    "perimeter_extrusion_width": "inner_wall_line_width",
    "external_perimeter_extrusion_width": "outer_wall_line_width",
    "first_layer_extrusion_width": "initial_layer_line_width",
    "infill_extrusion_width": "sparse_infill_line_width",
    "solid_infill_extrusion_width": "internal_solid_infill_line_width",
    "top_infill_extrusion_width": "top_surface_line_width",
    "support_material_extrusion_width": "support_line_width",
    "extrusion_width": "line_width",
    "external_perimeter_speed": "outer_wall_speed",
    "perimeter_speed": "inner_wall_speed",
}
# placeholders OrcaSlicer has no equivalent for: flagged for review
UNKNOWN_PLACEHOLDERS = {"idle_temperature", "first_layer_print_size", "filament_abrasive", "nozzle_high_flow", "extruder_id"}

FLAVORS = {"marlinlegacy": "marlin"}

SKIP = {"inherits", "compatible_printers", "compatible_printers_condition", "printer_settings_id", "printer_model",
        "printer_variant", "printer_vendor", "printer_technology", "host_type", "print_host", "printhost_apikey",
        "printhost_cafile", "printhost_password", "printhost_user", "printhost_port", "printhost_authorization_type",
        "printhost_ssl_ignore_revoke", "binary_gcode", "default_print_profile", "default_filament_profile", "renamed_from",
        "octoprint_apikey", "octoprint_host", "serial_port", "serial_speed", "physical_printer_settings_id"}


def parse_ini(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(("#", ";", "[")):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    if not out:
        raise ValueError("that file has no settings (export it from PrusaSlicer with File > Export > Export Config)")
    return out


def _unescape(v: str) -> str:
    # PrusaSlicer writes newlines in G-code as \n on one line; quotes may wrap the value
    if len(v) >= 2 and v[0] == v[-1] == '"':
        v = v[1:-1]
    return v.replace("\\n", "\n").replace("\\r", "").replace('\\"', '"').replace("\\\\", "\\")


def _rename_placeholders(code: str, found_unknown: set) -> str:
    """Rename placeholders only inside [...] and {...} macro parts, never in plain G-code or comments."""
    def ident(m):
        name = m.group(0)
        if name in UNKNOWN_PLACEHOLDERS:
            found_unknown.add(name)
        return PLACEHOLDERS.get(name, name)

    def part(m):
        body = m.group(0)
        # leave quoted strings inside macros alone
        pieces = re.split(r'("[^"]*")', body)
        return "".join(x if x.startswith('"') else re.sub(r"\b[a-z_][a-z0-9_]*\b", ident, x) for x in pieces)
    return re.sub(r"\[[^\]\n]*\]|\{[^}\n]*\}", part, code)


def _fill_literals(code: str, src: dict, filled: set) -> str:
    """Placeholders OrcaSlicer lacks but whose value the file states: write the value in (nozzle_high_flow[0] -> 1)."""
    def per_tool_bool(key):        # OrcaSlicer's macro language wants true/false here, not 1/0
        vals = [x.strip() in ("1", "true") for x in (src.get(key) or "").split(",") if x.strip()]
        return lambda i: "true" if (vals[i] if i < len(vals) else (vals[-1] if vals else False)) else "false"
    known = {"nozzle_high_flow": per_tool_bool("nozzle_high_flow"), "filament_abrasive": lambda i: "false"}

    def sub(m):
        filled.add(m.group(1))
        return known[m.group(1)](int(m.group(2)))
    return re.sub(r"\b(" + "|".join(known) + r")\[(\d+)\]", sub, code)


def prusa_ini_to_orca(text: str, base: str | None = None, name: str | None = None, keep_gcode: bool = False) -> dict:
    """keep_gcode False (the default when a base printer is given): the G-code comes from the OrcaSlicer base printer,
    which is known to work there. True: carry the PrusaSlicer G-code over, with placeholders renamed."""
    src = parse_ini(text)
    keep_gcode = keep_gcode or not base
    filled: set = set()
    if src.get("printer_technology", "FFF").upper() != "FFF":
        raise ValueError("only filament (FFF) printers can be converted")
    cfg: dict = {}
    mapped, unmapped, unknown = [], [], set()
    for k, v in src.items():
        if k in SKIP:
            continue
        ok = KEY_MAP.get(k)
        if not ok:
            unmapped.append(k)
            continue
        if ok in GCODE:
            if not keep_gcode:
                continue
            val = _rename_placeholders(_fill_literals(_unescape(v), src, filled), unknown)
        elif ok in VECTOR:
            val = [x.strip() for x in v.split(",") if x.strip() != ""] if v else []
        else:
            val = _unescape(v)
        if ok == "gcode_flavor":
            val = FLAVORS.get(val, val)
        if ok == "emit_machine_limits_to_gcode":
            val = "0" if val in ("ignore", "time_estimate_only") else "1"
        cfg[ok] = val
        mapped.append(k)
    if not mapped:
        raise ValueError("no printer settings found in that file; is it a PrusaSlicer printer or config export?")
    tools = len(cfg.get("nozzle_diameter") or []) or 1
    title = name or src.get("printer_settings_id") or src.get("printer_model") or "Imported printer"
    out = {"type": "machine", "name": f"{title} (from PrusaSlicer)", "from": "User", "instantiation": "true",
           "inherits": base or "", **cfg}
    warnings = []
    if not base:
        warnings.append("No OrcaSlicer printer to build on was picked; settings the file doesn't have will use OrcaSlicer defaults.")
    unknown -= filled
    if not keep_gcode:
        warnings.append(f"G-code (start, end, tool change) comes from the OrcaSlicer printer {base}, not from the file.")
    else:
        if filled:
            warnings.append("Filled in from the file because OrcaSlicer has no such setting: " + ", ".join(sorted(filled))
                            + " (filament_abrasive is taken as not abrasive).")
        if unknown:
            warnings.append("Check the G-code: these placeholders have no OrcaSlicer equivalent and will stop slicing: "
                            + ", ".join(sorted(unknown)))
        warnings.append("Carrying G-code over is best effort: printers with complex macros (such as the CORE One INDX) "
                        "usually need OrcaSlicer's own G-code. If the first slice fails, convert again without it.")
        if any(k in src for k in ("start_gcode", "end_gcode")):
            warnings.append("Start and end G-code were carried over with renamed placeholders; print a test before production.")
    printer_like = [k for k in unmapped if k in PRUSA_PRINTER_KEYS]
    return {"config": json.dumps(out, indent=2), "base": base, "tools": tools, "mapped": sorted(mapped), "keep_gcode": keep_gcode,
            "unmapped": sorted(printer_like), "ignored": len(unmapped) - len(printer_like), "warnings": warnings}
