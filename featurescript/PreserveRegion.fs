FeatureScript 3095;
import(path : "onshape/std/common.fs", version : "3095.0");

// Toolbar icon: featurescript/icons/preserve_region.svg, uploaded to this document as "Preserve region icon.svg".
PRESERVE_ICON::import(path : "d20121c2696a161418982641", version : "d8e2d3d2ad05bf6f734d2b4f");

// Preserve region (plan Phase 5).
//
// Marks faces of the design part (SPACE) whose geometry must survive topology optimization exactly.
// The selected faces are thickened into a keep solid that straddles them:
//   wall thickness t into the material (this is also the Mechanical Exclusion Thickness),
//   void margin m into the void (so the preserved face in final = (opt U keep) n space comes only
//   from SPACE's own tessellation, never from the keep's).
//
// One keep body per edge-connected group of faces, named KEEP_<ROLE>_<label>, or KEEP_<ROLE>_<label>_<k>
// when the selection has several groups. Each body carries a "PreserveRegion" attribute that the
// exporter reads (pipeline/onshape_export.py -> geometry/<part>/regions.json).
//
// opThicken convention (checked 2026-10-20): thickness1 runs along the face normal, which points out
// of the solid into the void; thickness2 runs into the material.

export enum PreserveRole
{
    annotation { "Name" : "FIX (fixed support)" }
    FIX,
    annotation { "Name" : "LOAD (remote force)" }
    LOAD,
    annotation { "Name" : "PASSIVE (no BC)" }
    PASSIVE
}

const WALL_BOUNDS = { (millimeter) : [0.01, 3.75, 1000] } as LengthBoundSpec;
const MARGIN_BOUNDS = { (millimeter) : [0.01, 0.5, 100] } as LengthBoundSpec;

const ATTRIBUTE_KIND = "PreserveRegion";

const ROLE_COLOURS = {
    PreserveRole.FIX : color(0.15, 0.45, 0.85, 0.55),
    PreserveRole.LOAD : color(0.85, 0.25, 0.2, 0.55),
    PreserveRole.PASSIVE : color(0.55, 0.55, 0.55, 0.55)
};

const ROLE_TITLES = { PreserveRole.FIX : "Fix", PreserveRole.LOAD : "Load", PreserveRole.PASSIVE : "Passive" };

// The tree shows e.g. "Preserve Fix 1": role and the resolved label, which make the feature's name unique.
annotation { "Feature Type Name" : "Preserve region",
             "Feature Name Template" : "Preserve #roleTitle #labelText",
             "Icon" : PRESERVE_ICON::BLOB_DATA,
             "Feature Type Description" : "Faces that topology optimization must keep exact. Creates KEEP_<ROLE>_<label> solids straddling the faces (wall t into the material, margin m into the void)." }
export const preserveRegion = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Faces to preserve", "Filter" : EntityType.FACE && ConstructionObject.NO && SketchObject.NO }
        definition.faces is Query;

        annotation { "Name" : "Role", "Default" : PreserveRole.FIX }
        definition.role is PreserveRole;

        annotation { "Name" : "Label (empty = next free number)", "Default" : "", "Description" : "A-Z, 0-9 and _ only. Leave empty to number this region automatically." }
        definition.label is string;

        annotation { "Name" : "Wall thickness (into material)", "Description" : "Wall that must stay solid; also the Exclusion Thickness" }
        isLength(definition.wallThickness, WALL_BOUNDS);

        annotation { "Name" : "Void margin (into void)", "Description" : "Must be smaller than any concave radius it grows into (e.g. a bore radius)" }
        isLength(definition.voidMargin, MARGIN_BOUNDS);
    }
    {
        setFeatureComputedParameter(context, id, { "name" : "roleTitle", "value" : ROLE_TITLES[definition.role] });
        definition.label = resolveLabel(context, definition.role, definition.label);
        setFeatureComputedParameter(context, id, { "name" : "labelText", "value" : definition.label });
        preserveRegionImpl(context, id, definition);
    }, { "role" : PreserveRole.FIX, "label" : "", "wallThickness" : 3.75 * millimeter, "voidMargin" : 0.5 * millimeter });

/**
 * The region label to use: the given one after validation, or, when it is empty, the lowest number not yet
 * used by an earlier Preserve region of the same role. Role + label names the region and its keep parts, so it
 * must be unique. Earlier Preserve regions are found through their attributes, which (unlike part names) are
 * readable during regeneration. An automatic label depends on feature order: deleting an earlier region of the
 * same role renumbers the later automatic ones.
 */
export function resolveLabel(context is Context, role is PreserveRole, label is string) returns string
{
    const roleName = toString(role);
    var used = {};
    for (var a in getAttributes(context, { "entities" : qEverything(EntityType.BODY) }))
    {
        if (a is map && a.kind == ATTRIBUTE_KIND && a.role == roleName)
            used[a.label] = true;
    }
    var free = 1;
    while (used[toString(free)] == true)
        free += 1;
    if (label == "")
        return toString(free);
    if (!match(label, "[A-Z0-9_]+").hasMatch)
        throw regenError("Label '" ~ label ~ "' is invalid: use A-Z, 0-9 and _ only, or leave it empty.", ["label"]);
    if (used[label] == true)
        throw regenError("Another Preserve region already uses " ~ roleName ~ " " ~ label ~ ". Use a different label (e.g. " ~ free ~ ") or leave it empty.", ["label"]);
    return label;
}

/** Body of the feature, separate so a test harness can call it with its own geometry. Expects a resolved label. */
export function preserveRegionImpl(context is Context, id is Id, definition is map)
{
    const faces = definition.faces;
    const faceList = evaluateQuery(context, faces);
    if (size(faceList) == 0)
        throw regenError("Select at least one face to preserve.", ["faces"]);

    const owners = evaluateQuery(context, qOwnerBody(faces));
    if (size(owners) != 1)
        throw regenError("Faces must all be on one part (they span " ~ size(owners) ~ ").", ["faces"], faces);

    const label = definition.label;
    const role = definition.role;
    const roleName = toString(role);
    const t = definition.wallThickness;
    const m = definition.voidMargin;

    // Whether the faces are on the part named SPACE is checked by the exporter: getProperty(NAME) is not
    // available while a feature regenerates (it always came back empty).

    const groups = connectedComponents(context, faces, AdjacencyType.EDGE);
    const nGroups = size(groups);
    const totalArea = evArea(context, { "entities" : faces });

    for (var k = 0; k < nGroups; k += 1)
    {
        const group = qUnion(groups[k]);
        const thickenId = id + ("keep" ~ k);
        try
        {
            opThicken(context, thickenId, {
                        "entities" : group,
                        "thickness1" : m, // along the outward normal: into the void
                        "thickness2" : t  // into the material
                    });
        }
        catch
        {
            throw regenError("Thicken failed. The void margin may be too large for a concave face (it must be smaller than e.g. a bore radius), or the wall may self-intersect.",
                             ["voidMargin", "wallThickness"], group);
        }
        const bodies = qCreatedBy(thickenId, EntityType.BODY);
        if (size(evaluateQuery(context, bodies)) != 1)
            throw regenError("Thicken of a connected face group did not give exactly one body.", ["faces"], group);

        const name = "KEEP_" ~ roleName ~ "_" ~ label ~ (nGroups > 1 ? "_" ~ (k + 1) : "");
        setProperty(context, { "entities" : bodies, "propertyType" : PropertyType.NAME, "value" : name });
        setProperty(context, { "entities" : bodies, "propertyType" : PropertyType.APPEARANCE, "value" : ROLE_COLOURS[role] });
        setAttribute(context, {
                    "entities" : bodies,
                    "attribute" : {
                        "kind" : ATTRIBUTE_KIND,
                        "name" : name,
                        "role" : roleName,
                        "label" : label,
                        "group" : k + 1,
                        "groupCount" : nGroups,
                        "wallThicknessMm" : t / millimeter,
                        "voidMarginMm" : m / millimeter,
                        "faceCount" : size(groups[k]),
                        "areaMm2" : evArea(context, { "entities" : group }) / millimeter ^ 2,
                        "totalFaceCount" : size(faceList),
                        "totalAreaMm2" : totalArea / millimeter ^ 2
                    }
                });
    }
}
