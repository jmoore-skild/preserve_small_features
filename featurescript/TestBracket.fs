FeatureScript 3095;
import(path : "onshape/std/common.fs", version : "3095.0");

// Test L-bracket (plan Phase 5): covers what the beam doesn't, for marking with the Preserve region feature.
//
// Frame: base plate on z = 0 spanning X, upright plate on x = 0 spanning Z, width W along Y.
// Creates one part, SPACE, with:
//   - two plain through-holes in the base, axis Z (bolts to the ground),
//   - a counterbored through-hole in the base, counterbore from the top face,
//   - two through-holes in the upright, axis X (a second, non-parallel hole axis),
//   - a through-slot in the upright, axis X, running along Z,
//   - a mating pad on the upright's outer face (x = 0): a split face around the upright holes,
//   - an inner fillet between base and upright.
// No keeps are made here; the user marks the faces with Preserve region in the GUI.

const LEN_90 = { (millimeter) : [10, 90, 10000] } as LengthBoundSpec;
const LEN_80 = { (millimeter) : [10, 80, 10000] } as LengthBoundSpec;
const LEN_60 = { (millimeter) : [10, 60, 10000] } as LengthBoundSpec;
const LEN_8 = { (millimeter) : [1, 8, 1000] } as LengthBoundSpec;
const LEN_6 = { (millimeter) : [0.1, 6, 1000] } as LengthBoundSpec;

annotation { "Feature Type Name" : "Test bracket",
             "Feature Type Description" : "L-bracket test part (SPACE only) with holes on two axes, a counterbore, a slot and a split mating pad." }
export const testBracket = defineFeature(function(context is Context, id is Id, definition is map)
    precondition
    {
        annotation { "Name" : "Base length (X)" }
        isLength(definition.baseLength, LEN_90);
        annotation { "Name" : "Upright height (Z)" }
        isLength(definition.uprightHeight, LEN_80);
        annotation { "Name" : "Width (Y)" }
        isLength(definition.width, LEN_60);
        annotation { "Name" : "Plate thickness" }
        isLength(definition.thickness, LEN_8);
        annotation { "Name" : "Inner fillet radius" }
        isLength(definition.filletRadius, LEN_6);
    }
    {
        const mm = millimeter;
        const L = definition.baseLength;
        const H = definition.uprightHeight;
        const W = definition.width;
        const t = definition.thickness;

        // Features are placed relative to the plates so the part scales sensibly.
        const baseHoleD = 6.6 * mm;      // M6 clearance
        const cboreD = 11 * mm;
        const cboreDepth = t / 2;
        const upHoleD = 5.5 * mm;        // M5 clearance
        const slotW = 5.5 * mm;
        const xFix = L - 20 * mm;        // plain base holes
        const xCb = (L + t) / 2;         // counterbored base hole
        const zUp = H - 18 * mm;         // upright holes
        const ySide = W * 0.2;
        const zSlot0 = t + definition.filletRadius + 12 * mm;
        const zSlot1 = zSlot0 + 15 * mm;
        if (xCb + cboreD / 2 > xFix - baseHoleD / 2 - 3 * mm || zSlot1 + slotW / 2 > zUp - upHoleD / 2 - 3 * mm)
            throw regenError("Plates too small for the test features.");

        // L body.
        fCuboid(context, id + "base", { "corner1" : vector(0 * mm, 0 * mm, 0 * mm), "corner2" : vector(L, W, t) });
        fCuboid(context, id + "up", { "corner1" : vector(0 * mm, 0 * mm, 0 * mm), "corner2" : vector(t, W, H) });
        opBoolean(context, id + "join", {
                    "tools" : qUnion([qCreatedBy(id + "base", EntityType.BODY), qCreatedBy(id + "up", EntityType.BODY)]),
                    "operationType" : BooleanOperationType.UNION
                });
        const body = qCreatedBy(id + "base", EntityType.BODY);

        // Inner fillet: the concave edge along Y at (t, *, t).
        opFillet(context, id + "fillet", {
                    "entities" : qContainsPoint(qOwnedByBody(body, EntityType.EDGE), vector(t, W / 2, t)),
                    "radius" : definition.filletRadius
                });

        // Mating pad: split the upright's outer face around the upright holes (before drilling, so the
        // pad is one face with holes in it).
        const padY0 = ySide - 10 * mm;
        const padY1 = W - ySide + 10 * mm;
        const padZ0 = zUp - 10 * mm;
        const padZ1 = min(H - 2 * mm, zUp + 10 * mm);
        fCuboid(context, id + "padTool", { "corner1" : vector(-1 * mm, padY0, padZ0), "corner2" : vector(1 * mm, padY1, padZ1) });
        opSplitFace(context, id + "pad", {
                    "faceTargets" : qContainsPoint(qOwnedByBody(body, EntityType.FACE), vector(0 * mm, W / 2, H / 2)),
                    "bodyTools" : qCreatedBy(id + "padTool", EntityType.BODY)
                });
        opDeleteBodies(context, id + "delPadTool", { "entities" : qCreatedBy(id + "padTool", EntityType.BODY) });

        // Cutters (cylinders), all subtracted in one boolean.
        var cyls = [];
        for (var y in [ySide, W - ySide]) // base holes, axis Z
            cyls = append(cyls, [vector(xFix, y, -1 * mm), vector(xFix, y, t + 1 * mm), baseHoleD / 2]);
        cyls = append(cyls, [vector(xCb, W / 2, -1 * mm), vector(xCb, W / 2, t + 1 * mm), baseHoleD / 2]);
        cyls = append(cyls, [vector(xCb, W / 2, t - cboreDepth), vector(xCb, W / 2, t + 1 * mm), cboreD / 2]);
        for (var y in [ySide, W - ySide]) // upright holes, axis X
            cyls = append(cyls, [vector(-1 * mm, y, zUp), vector(t + 1 * mm, y, zUp), upHoleD / 2]);
        for (var z in [zSlot0, zSlot1]) // slot ends, axis X
            cyls = append(cyls, [vector(-1 * mm, W / 2, z), vector(t + 1 * mm, W / 2, z), slotW / 2]);
        for (var i = 0; i < size(cyls); i += 1)
            fCylinder(context, id + "cut" + toString(i), { "bottomCenter" : cyls[i][0], "topCenter" : cyls[i][1], "radius" : cyls[i][2] });
        fCuboid(context, id + "cut" + "slot", { // slot body between the ends
                    "corner1" : vector(-1 * mm, W / 2 - slotW / 2, zSlot0),
                    "corner2" : vector(t + 1 * mm, W / 2 + slotW / 2, zSlot1)
                });
        opBoolean(context, id + "drill", {
                    "tools" : qCreatedBy(id + "cut", EntityType.BODY),
                    "targets" : body,
                    "operationType" : BooleanOperationType.SUBTRACTION
                });

        setProperty(context, { "entities" : body, "propertyType" : PropertyType.NAME, "value" : "SPACE" });
    }, { "baseLength" : 90 * millimeter, "uprightHeight" : 80 * millimeter, "width" : 60 * millimeter,
         "thickness" : 8 * millimeter, "filletRadius" : 6 * millimeter });
