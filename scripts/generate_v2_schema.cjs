// Compile the public TypeScript wire contract into closed JSON Schema.
// No network or package installation; uses the dashboard's pinned TypeScript compiler.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const ts = require('../frontend/v2/node_modules/typescript');
const root = path.resolve(__dirname, '..');
const input = path.join(root, 'dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts');
const source = fs.readFileSync(input, 'utf8');
const ast = ts.createSourceFile(input, source, ts.ScriptTarget.Latest, true);
const declarations = new Map(ast.statements.filter(s => ts.isInterfaceDeclaration(s) || ts.isTypeAliasDeclaration(s)).map(s => [s.name.text, s]));
const defs = {};
const primitives = {StringKeyword:'string', NumberKeyword:'number', BooleanKeyword:'boolean', NullKeyword:'null'};
function object(members, env) {
  const properties = {}, required = [];
  let additionalProperties = false;
  for (const member of members) {
    if (ts.isIndexSignatureDeclaration(member)) { additionalProperties = convert(member.type, env); continue; }
    if (!ts.isPropertySignature(member)) throw new Error('Unsupported member: '+member.getText(ast));
    const name = member.name.text;
    properties[name] = convert(member.type, env);
    if (!member.questionToken) required.push(name);
  }
  return {type:'object', properties, required, additionalProperties};
}
function definition(name, args=[]) {
  const key = name + (args.length ? '__'+crypto.createHash('sha256').update(JSON.stringify(args)).digest('hex').slice(0,12) : '');
  if (defs[key]) return {$ref:'#/$defs/'+key};
  const node = declarations.get(name);
  if (!node) throw new Error('Unknown type '+name);
  defs[key] = {};
  const env = Object.fromEntries((node.typeParameters || []).map((p,i) => [p.name.text, args[i] || {}]));
  let schema;
  if (ts.isInterfaceDeclaration(node)) {
    schema = object(node.members, env);
    for (const clause of node.heritageClauses || []) for (const parent of clause.types) {
      const ref = definition(parent.expression.text, (parent.typeArguments || []).map(n => convert(n, env)));
      const base = defs[ref.$ref.split('/').pop()];
      schema.properties = {...base.properties, ...schema.properties};
      schema.required = [...new Set([...(base.required || []), ...schema.required])];
    }
  } else schema = convert(node.type, env);
  if (name === 'Count') schema = {type:'integer', minimum:0, maximum:Number.MAX_SAFE_INTEGER};
  if (name === 'Instant') schema = {type:'string', format:'date-time', pattern:'Z$'};
  if (name === 'Day') schema = {type:'string', format:'date'};
  if (name === 'DecimalString') schema = {type:'string', pattern:'^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?$'};
  defs[key] = schema;
  return {$ref:'#/$defs/'+key};
}
function convert(node, env={}) {
  const primitive = primitives[ts.SyntaxKind[node.kind]];
  if (primitive) return {type:primitive};
  if (ts.isParenthesizedTypeNode(node)) return convert(node.type,env);
  if (ts.isLiteralTypeNode(node)) {
    const v = node.literal;
    if (v.kind === ts.SyntaxKind.NullKeyword) return {type:'null'};
    return {const: ts.isStringLiteral(v) ? v.text : v.kind === ts.SyntaxKind.TrueKeyword ? true : v.kind === ts.SyntaxKind.FalseKeyword ? false : Number(v.text)};
  }
  if (ts.isUnionTypeNode(node)) return {anyOf:node.types.map(n=>convert(n,env))};
  if (ts.isArrayTypeNode(node)) return {type:'array',items:convert(node.elementType,env)};
  if (ts.isTypeLiteralNode(node)) return object(node.members,env);
  if (ts.isIndexedAccessTypeNode(node)) {
    const reference = convert(node.objectType, env);
    return defs[reference.$ref.split('/').pop()].properties[convert(node.indexType,env).const];
  }
  if (ts.isTypeReferenceNode(node)) {
    const name = node.typeName.getText(ast);
    if (env[name]) return env[name];
    const args = (node.typeArguments || []).map(n=>convert(n,env));
    if (name === 'Record') return {type:'object', additionalProperties:args[1]};
    if (name === 'Partial') return {...(args[0].$ref ? defs[args[0].$ref.split('/').pop()] : args[0]), required:[]};
    if (name === 'Pick' || name === 'Omit') {
      const original = defs[args[0].$ref.split('/').pop()];
      const keys = args[1].anyOf ? args[1].anyOf.map(a=>a.const) : [args[1].const];
      const properties = Object.fromEntries(Object.entries(original.properties).filter(([key]) => name === 'Pick' ? keys.includes(key) : !keys.includes(key)));
      return {...original, properties, required:original.required.filter(key=>key in properties)};
    }
    return definition(name,args);
  }
  throw new Error('Unsupported type: '+node.getText(ast));
}
for (const [name,node] of declarations) if (!node.typeParameters?.length) definition(name);
// Endpoint envelopes are concrete and are validated in both Python and OpenAPI.
for (const [name,node] of declarations) if (!node.typeParameters?.length && name !== 'Json') {
  const item = definition(name);
  defs[name+'Response'] = {type:'object',properties:{data:item,meta:definition('Meta')}, required:['data','meta'],additionalProperties:false};
  defs[name+'Page'] = {...defs[definition('Page',[item]).$ref.split('/').pop()]};
}
const output = { $schema:'https://json-schema.org/draft/2020-12/schema', source_sha256:crypto.createHash('sha256').update(source).digest('hex'), $defs:defs };
const destination = path.join(root,'src/doxagent/api_v2/wire_schema.json');
fs.mkdirSync(path.dirname(destination),{recursive:true});
fs.writeFileSync(destination,JSON.stringify(output,null,2)+'\n');
const contract = fs.readFileSync(path.join(root,'dev_plan/workflow_v2/DOXAGENT_V2_API_CONTRACT.md'),'utf8');
const routes = [];
for (const line of contract.split(/\r?\n/)) {
  const match = line.match(/^\| (GET|POST|PATCH|PUT|DELETE) \| `([^`]+)` \| (.*?) \| (.*?) \|$/);
  if (!match) continue;
  const [,method,url,parameters,description] = match;
  let schema = null;
  if (description.includes('Pick<ShellSummary')) schema = 'PolicyShellSummaryPage';
  else if (description.match(/Page<([A-Za-z]+)>/)) schema = RegExp.$1+'Page';
  else if (description.includes('Operation')) schema = 'Operation';
  else if (description.includes('JSON 下载')) schema = 'PolicySetDownload';
  else if (description.match(/Response<(?!T>)([A-Za-z]+)>/)) schema = RegExp.$1;
  else if (!description.startsWith('ZIP') && !description.startsWith('SSE')) schema = description.match(/[A-Z][A-Za-z]+/)?.[0];
  if (schema && !defs[schema]) throw new Error('Unknown route schema '+schema+' '+url);
  routes.push({method,path:url,parameters,response_schema:schema,
    transport:description.startsWith('SSE')?'SSE':description.startsWith('ZIP')?'ZIP':'JSON'});
}
fs.writeFileSync(path.join(root,'src/doxagent/api_v2/route_contract.json'),JSON.stringify(routes,null,2)+'\n');
console.log(JSON.stringify({definitions:Object.keys(defs).length, output:destination}));
