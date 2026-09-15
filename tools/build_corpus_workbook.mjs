import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [csvPath, outputPath, previewPath] = process.argv.slice(2);
const csvText = (await fs.readFile(csvPath, "utf8")).replace(/^\uFEFF/, "");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "Episodes" });
const sheet = workbook.worksheets.getItem("Episodes");
const used = sheet.getUsedRange(true);
const values = used.values;
const rowCount = values.length;
const columnCount = values[0].length;
const columnName = (number) => {
  let result = "";
  for (let n = number; n > 0; n = Math.floor((n - 1) / 26)) {
    result = String.fromCharCode(65 + ((n - 1) % 26)) + result;
  }
  return result;
};
const lastColumn = columnName(columnCount);

for (let row = 1; row < rowCount; row++) {
  if (values[row][3]) values[row][3] = new Date(values[row][3]);
  for (const col of [4, 5, 6, 7, 8, 9]) {
    if (values[row][col] !== "" && values[row][col] !== null) values[row][col] = Number(values[row][col]);
  }
}
used.values = values;
sheet.name = "Robotics episodes";
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(1);

used.format.font = { name: "Arial", size: 10, color: "#172033" };
used.format.verticalAlignment = "center";
sheet.getRange(`A1:${lastColumn}1`).format = {
  fill: "#263A5B",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
};
sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 30;
sheet.getRange(`D2:D${rowCount}`).setNumberFormat("yyyy-mm-dd hh:mm");
sheet.getRange(`E2:E${rowCount}`).setNumberFormat("0.0");
sheet.getRange(`F2:G${rowCount}`).setNumberFormat("#,##0");
sheet.getRange(`H2:H${rowCount}`).setNumberFormat("0.0");
sheet.getRange(`I2:J${rowCount}`).setNumberFormat("0");
sheet.getRange(`A2:${lastColumn}${rowCount}`).format.rowHeight = 20;

const widths = [24, 44, 24, 20, 14, 13, 15, 14, 10, 10, 30, 38, 18, 42, 44, 44, 80];
for (let col = 0; col < widths.length; col++) {
  sheet.getRangeByIndexes(0, col, rowCount, 1).format.columnWidth = widths[col];
}
sheet.getRange(`B2:C${rowCount}`).format.wrapText = false;
sheet.getRange(`K2:${lastColumn}${rowCount}`).format.wrapText = false;
sheet.tables.add(`A1:${lastColumn}${rowCount}`, true, "RoboticsEpisodes").style = "TableStyleMedium2";

workbook.recalculate();
const inspection = await workbook.inspect({
  kind: "table", range: `Robotics episodes!A1:${lastColumn}12`, include: "values,formulas",
  tableMaxRows: 12, tableMaxCols: columnCount, maxChars: 6000,
});
console.log(inspection.ndjson);
const errors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 50 }, summary: "final formula error scan",
});
console.log(errors.ndjson);
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
