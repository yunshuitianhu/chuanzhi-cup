/** Strictly typecheck compiled TypeScript SFC scripts without writing temporary files. */
import fs from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'

const root=fileURLToPath(new URL('..',import.meta.url))
const normalize=name=>path.resolve(name).replaceAll('\\','/')
const sources=new Map()
function collect(directory) {
  for(const entry of fs.readdirSync(directory,{withFileTypes:true})) {
    const filename=path.join(directory,entry.name)
    if(entry.isDirectory())collect(filename)
    else if(entry.name.endsWith('.vue')) {
      const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename})
      if(errors.length)throw new Error('Invalid SFC: '+filename)
      if(descriptor.scriptSetup?.lang==='ts'||descriptor.script?.lang==='ts') {
        sources.set(normalize(filename+'.__script.ts'),compileScript(descriptor,{id:filename}).content)
      }
    }
  }
}
collect(path.join(root,'src'))
const config=ts.readConfigFile(path.join(root,'tsconfig.json'),ts.sys.readFile)
if(config.error)throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText,'\n'))
const parsed=ts.parseJsonConfigFileContent(config.config,ts.sys,root)
const host=ts.createCompilerHost(parsed.options)
const getSourceFile=host.getSourceFile.bind(host),fileExists=host.fileExists.bind(host),readFile=host.readFile.bind(host)
host.getSourceFile=(name,version,onError,createNew)=>sources.has(normalize(name))
  ?ts.createSourceFile(name,sources.get(normalize(name)),version,true):getSourceFile(name,version,onError,createNew)
host.fileExists=name=>sources.has(normalize(name))||fileExists(name)
host.readFile=name=>sources.get(normalize(name))??readFile(name)
const program=ts.createProgram([...parsed.fileNames,...sources.keys()],parsed.options,host)
const diagnostics=[...parsed.errors,...ts.getPreEmitDiagnostics(program)]
if(diagnostics.length) {
  process.stderr.write(ts.formatDiagnosticsWithColorAndContext(diagnostics,{getCanonicalFileName:f=>f,getCurrentDirectory:()=>root,getNewLine:()=> '\n'}))
  process.exitCode=1
}else process.stdout.write(`Strict TypeScript SFC script checks passed: ${sources.size} component(s).\n`)
