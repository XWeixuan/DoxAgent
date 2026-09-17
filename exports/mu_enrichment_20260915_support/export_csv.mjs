import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook } from '@oai/artifact-tool';

const folder=path.dirname(fileURLToPath(import.meta.url));
const prepared=JSON.parse(await fs.readFile(path.join(folder,'prepared_table.json'),'utf8'));
const matrix=[prepared.headers,...prepared.rows];
const workbook=Workbook.create();
if(process.argv.includes('--help-values')) {
  console.log(workbook.help('range.values', {include:'index,notes,examples',maxChars:4000}).ndjson);
  process.exit(0);
}
const sheet=workbook.worksheets.add('Failures');
sheet.getRangeByIndexes(0,0,matrix.length,prepared.headers.length).setNumberFormat('@');
sheet.getRangeByIndexes(0,0,matrix.length,prepared.headers.length).values=matrix;
workbook.recalculate();
const values=sheet.getRangeByIndexes(0,0,matrix.length,prepared.headers.length).values;
if (values.length!==matrix.length) throw new Error('row count changed');
const cell=value=>value===null||value===undefined?'':String(value);
for(let row=0;row<matrix.length;row++) {
  for(let col=0;col<prepared.headers.length;col++) {
    if(values[row][col] instanceof Date && typeof matrix[row][col]==='string'
      && values[row][col].getTime()===new Date(matrix[row][col]).getTime()) continue;
    if(cell(values[row][col])!==cell(matrix[row][col])) throw new Error(`value changed ${row},${col}: ${cell(matrix[row][col])} -> ${cell(values[row][col])}`);
  }
}
const quote=value=>'"'+cell(value).replaceAll('"','""')+'"';
// CSV has no typed-date cells: retain original UTC spellings after workbook validation.
const csv='\uFEFF'+matrix.map(row=>row.map(quote).join(',')).join('\r\n')+'\r\n';
const output=path.join(folder,'..','MU_published_body_enrichment_failed_72h_url_title_dedup_20260915.csv');
await fs.writeFile(output,csv,'utf8');
console.log(JSON.stringify({output,rows:prepared.rows.length,columns:prepared.headers.length}));
