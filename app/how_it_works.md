## What it reads

**DXF.** Flat patterns with lines, arcs, circles, polylines (including curved segments), splines and ellipses. Text, dimensions and anything on a layer whose name says bend, etch, mark, notes or dims is left out, because the laser doesn't cut it. Dashed lines are left out too, since SolidWorks and most CAD programs export bend lines that way.

**PDF.** A print exported from CAD keeps its lines inside the file, so they can be measured. The page border is dropped, the biggest closed shape left on the page is taken as the part's outline, and only the holes and cutouts inside it are kept. Dimensions, centre marks, notes and the title block are therefore left out. A print at 1:1 needs nothing else. For a scaled print, type the part's real width and everything is scaled to match. A scanned PDF only holds a picture, so the tool says so and asks for the DXF.

**STEP.** A 3D model is checked to see if it is a flat plate (two big faces at the same distance apart, and every other face standing square to them). If it is, the top face's outline and holes are the cut path and the plate thickness is read from the model. A part with bends, bevels or countersinks is flagged as needing a flat pattern and isn't priced.

## How the numbers are worked out

- **Cut length** is the total length of every cut. Lines, arcs and circles are measured exactly, and curves along the true curve.
- **Pierces.** Loose lines and arcs are joined end to end into closed outlines, and each outline is one pierce. Anything inside the outside profile is a hole or cutout.
- **Messy files** are cleaned before counting. Lines drawn twice are removed so they aren't cut twice, and tiny gaps (up to 0.002 in) are closed. Open outlines, several parts in one file and holes smaller than the material thickness are flagged for someone to check.
- **Run time** = cut length ÷ cut speed + pierces × pierce time, plus an allowance for the head moving between features.
- **Sheet use** lays the parts out in a simple grid on your standard sheet, both ways round, and counts how many fit. Real nesting packs tighter, so this errs on the safe side.
- **Price each** = run time × machine rate + material + handling. Material can be charged by the rectangle each part needs, by the full sheets the job uses, or by the net part area. Setup is added once per job, and the minimum charge applies to the whole job.

## Rates

Every rate and charge is on the Rates page, which also has the Excel workbook to download. The numbers in it now are only examples for the demo. On your shop PC the estimator reads the workbook each time it starts, so changing a rate means editing a cell in Excel and saving.
