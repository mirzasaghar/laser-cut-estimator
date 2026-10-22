#### What it reads

This demo reads flat-pattern DXF files. It understands lines, arcs, circles, polylines (including curved segments), splines and ellipses. Text, dimensions and anything on a layer whose name says bend, etch, mark, notes or dims is left out, because the laser doesn't cut it. Dashed lines are left out too, since SolidWorks and most CAD programs export bend lines that way.

#### How the numbers are worked out

- **Cut length** is the total length of every cut. Lines, arcs and circles are measured exactly. Splines and ellipses are measured along the true curve in steps of a ten-thousandth of an inch.
- **Pierces.** Loose lines and arcs are joined end to end into closed outlines, and each outline is one pierce. The largest outline is the outside of the part. Anything inside it is a hole or a cutout (a part sitting inside a cutout is counted as a part again).
- **Messy customer files** are cleaned before counting. Lines drawn twice are removed so they aren't cut twice, and tiny gaps (up to 0.002 in) are closed. Open outlines, several parts in one file and holes smaller than the material thickness are flagged so someone can check them.
- **Run time** = cut length ÷ cut speed + pierces × pierce time, plus an allowance for the head moving between features.
- **Price each** = run time × machine rate + material (the rectangle the part needs, or the net part area) with markup + handling. Setup is added once per job, and the minimum charge applies to the job, not to each part.

Every rate and charge is on the Rates and charges tab. The numbers in it now are only examples so the demo works.

#### What the full version adds

- Your own spreadsheet's rates and formulas, so the tool prices the way you already do.
- **PDF prints.** A PDF exported from CAD has the lines stored inside it, so the same measuring works on it. A scanned PDF only has a picture, so the tool says so and asks for a DXF instead of guessing.
- **STEP files.** For a flat part the tool finds the large flat face and measures its outline. A bent part needs its flat pattern first, so the tool marks it as needing unfolding rather than giving a wrong price.
- It runs on a shop PC as a desktop shortcut, with the rates kept in an Excel file you can edit.
