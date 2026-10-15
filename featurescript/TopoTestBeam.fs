FeatureScript 3095;
import(path : "onshape/std/common.fs", version : "3095.0");

// Topo test beam (plan Phase 1).
//
// Frame: X along the beam (root face at x = 0), Y through the thickness, Z vertical.
// All bores are through-holes parallel to Y.
//
// Parts created (names are what the exporter looks up):
//   SPACE                                  box minus all through-bores (the FE design geometry)
//   KEEP_FIX_1..n, KEEP_LOAD_1..n,         solid full-thickness cylinders coaxial with each bore
//   KEEP_PASSIVE_1..n                      (they overlap SPACE; that is intended)
//
// Bore numbering within a zone: X varies fastest, then Z, matching config/base.yaml.
// Pattern positions: centre + (i - (count - 1) / 2) * pitch along each axis.

const LEN_200 = { (millimeter) : [1, 200, 10000] } as LengthBoundSpec;
const LEN_60 = { (millimeter) : [1, 60, 10000] } as LengthBoundSpec;
const LEN_15 = { (millimeter) : [0.1, 15, 10000] } as LengthBoundSpec;
const LEN_4_5 = { (millimeter) : [0.1, 4.5, 1000] } as LengthBoundSpec;
const LEN_12 = { (millimeter) : [0.1, 12, 1000] } as LengthBoundSpec;

const POS_20 = { (millimeter) : [0, 20, 10000] } as LengthBoundSpec;
const POS_30 = { (millimeter) : [0, 30, 10000] } as LengthBoundSpec;
const POS_10 = { (millimeter) : [0, 10, 10000] } as LengthBoundSpec;
const POS_100 = { (millimeter) : [0, 100, 10000] } as LengthBoundSpec;
const POS_190 = { (millimeter) : [0, 190, 10000] } as LengthBoundSpec;

const COUNT_1 = { (unitless) : [1, 1, 50] } as IntegerBoundSpec;
const COUNT_2 = { (unitless) : [1, 2, 50] } as IntegerBoundSpec;

annotation { "Feature Type Name" : "Topo test beam",
             "Feature Type Description" : "Cantilever test beam: SPACE (box minus bores) and solid KEEP_* cylinders for exclude-and-rebuild TO." }
export const topoTestBeam = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        // Envelope
        annotation { "Name" : "Length (X)" }
        isLength(definition.length, LEN_200);
        annotation { "Name" : "Height (Z)" }
        isLength(definition.height, LEN_60);
        annotation { "Name" : "Thickness (Y)" }
        isLength(definition.thickness, LEN_15);

        // Features
        annotation { "Name" : "Bore diameter" }
        isLength(definition.boreDiameter, LEN_4_5);
        annotation { "Name" : "Keep diameter" }
        isLength(definition.keepDiameter, LEN_12);

        // FIX pattern (root): 2 x 2 about (20, 30), pitch 20
        annotation { "Name" : "FIX centre X" }
        isLength(definition.fixCx, POS_20);
        annotation { "Name" : "FIX centre Z" }
        isLength(definition.fixCz, POS_30);
        annotation { "Name" : "FIX count X" }
        isInteger(definition.fixNx, COUNT_2);
        annotation { "Name" : "FIX count Z" }
        isInteger(definition.fixNz, COUNT_2);
        annotation { "Name" : "FIX pitch X" }
        isLength(definition.fixPx, POS_20);
        annotation { "Name" : "FIX pitch Z" }
        isLength(definition.fixPz, POS_20);

        // LOAD pattern (tip): 1 x 2 about (190, 30), pitch 20
        annotation { "Name" : "LOAD centre X" }
        isLength(definition.loadCx, POS_190);
        annotation { "Name" : "LOAD centre Z" }
        isLength(definition.loadCz, POS_30);
        annotation { "Name" : "LOAD count X" }
        isInteger(definition.loadNx, COUNT_1);
        annotation { "Name" : "LOAD count Z" }
        isInteger(definition.loadNz, COUNT_2);
        annotation { "Name" : "LOAD pitch X" }
        isLength(definition.loadPx, POS_20);
        annotation { "Name" : "LOAD pitch Z" }
        isLength(definition.loadPz, POS_20);

        // PASSIVE pattern (bottom chord, mid-span): 2 x 1 about (100, 10), pitch 20
        annotation { "Name" : "PASSIVE centre X" }
        isLength(definition.passiveCx, POS_100);
        annotation { "Name" : "PASSIVE centre Z" }
        isLength(definition.passiveCz, POS_10);
        annotation { "Name" : "PASSIVE count X" }
        isInteger(definition.passiveNx, COUNT_2);
        annotation { "Name" : "PASSIVE count Z" }
        isInteger(definition.passiveNz, COUNT_1);
        annotation { "Name" : "PASSIVE pitch X" }
        isLength(definition.passivePx, POS_20);
        annotation { "Name" : "PASSIVE pitch Z" }
        isLength(definition.passivePz, POS_20);
    }
    {
        const L = definition.length;
        const H = definition.height;
        const T = definition.thickness;
        const r = definition.boreDiameter / 2;
        const R = definition.keepDiameter / 2;
        if (R <= r)
            throw regenError("Keep diameter must be larger than bore diameter.", ["keepDiameter", "boreDiameter"]);

        const zones = [
            { "name" : "FIX", "cx" : definition.fixCx, "cz" : definition.fixCz, "nx" : definition.fixNx,
              "nz" : definition.fixNz, "px" : definition.fixPx, "pz" : definition.fixPz },
            { "name" : "LOAD", "cx" : definition.loadCx, "cz" : definition.loadCz, "nx" : definition.loadNx,
              "nz" : definition.loadNz, "px" : definition.loadPx, "pz" : definition.loadPz },
            { "name" : "PASSIVE", "cx" : definition.passiveCx, "cz" : definition.passiveCz, "nx" : definition.passiveNx,
              "nz" : definition.passiveNz, "px" : definition.passivePx, "pz" : definition.passivePz }
        ];

        // Bore centres, X fastest then Z within each zone.
        var centres = [];
        for (var zone in zones)
        {
            var k = 1;
            for (var j = 0; j < zone.nz; j += 1)
            {
                for (var i = 0; i < zone.nx; i += 1)
                {
                    const x = zone.cx + (i - (zone.nx - 1) / 2) * zone.px;
                    const z = zone.cz + (j - (zone.nz - 1) / 2) * zone.pz;
                    const name = zone.name ~ "_" ~ toString(k);
                    if (x - R < 0 * meter || x + R > L || z - R < 0 * meter || z + R > H)
                        throw regenError("Keep zone " ~ name ~ " extends outside the envelope.");
                    centres = append(centres, { "name" : name, "x" : x, "z" : z });
                    k += 1;
                }
            }
        }

        // SPACE: box minus through-bores (bores overshoot both faces by 1 mm for a clean cut).
        fCuboid(context, id + "space", {
                    "corner1" : vector(0, 0, 0) * meter,
                    "corner2" : vector(L, T, H)
                });
        for (var n = 0; n < size(centres); n += 1)
        {
            const c = centres[n];
            fCylinder(context, id + "bores" + toString(n), {
                        "bottomCenter" : vector(c.x, -1 * millimeter, c.z),
                        "topCenter" : vector(c.x, T + 1 * millimeter, c.z),
                        "radius" : r
                    });
        }
        opBoolean(context, id + "subtract", {
                    "tools" : qCreatedBy(id + "bores", EntityType.BODY),
                    "targets" : qCreatedBy(id + "space", EntityType.BODY),
                    "operationType" : BooleanOperationType.SUBTRACTION
                });
        setProperty(context, {
                    "entities" : qCreatedBy(id + "space", EntityType.BODY),
                    "propertyType" : PropertyType.NAME,
                    "value" : "SPACE"
                });

        // KEEP_*: solid full-thickness cylinders, created after the subtraction so they stay separate parts.
        for (var n = 0; n < size(centres); n += 1)
        {
            const c = centres[n];
            const keepId = id + "keep" + toString(n);
            fCylinder(context, keepId, {
                        "bottomCenter" : vector(c.x, 0 * meter, c.z),
                        "topCenter" : vector(c.x, T, c.z),
                        "radius" : R
                    });
            setProperty(context, {
                        "entities" : qCreatedBy(keepId, EntityType.BODY),
                        "propertyType" : PropertyType.NAME,
                        "value" : "KEEP_" ~ c.name
                    });
        }
    }, {
        "length" : 200 * millimeter, "height" : 60 * millimeter, "thickness" : 15 * millimeter,
        "boreDiameter" : 4.5 * millimeter, "keepDiameter" : 12 * millimeter,
        "fixCx" : 20 * millimeter, "fixCz" : 30 * millimeter, "fixNx" : 2, "fixNz" : 2,
        "fixPx" : 20 * millimeter, "fixPz" : 20 * millimeter,
        "loadCx" : 190 * millimeter, "loadCz" : 30 * millimeter, "loadNx" : 1, "loadNz" : 2,
        "loadPx" : 20 * millimeter, "loadPz" : 20 * millimeter,
        "passiveCx" : 100 * millimeter, "passiveCz" : 10 * millimeter, "passiveNx" : 2, "passiveNz" : 1,
        "passivePx" : 20 * millimeter, "passivePz" : 20 * millimeter
    });
