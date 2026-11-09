#!/usr/bin/env python3
"""Count the target-function source variant, preserving a per-file audit trail."""
from pathlib import Path
import argparse
import ast
import csv
import hashlib
import io
import json
import tokenize

def clean_c(text):
    out=[]; i=0; state='normal'; quote=''
    while i<len(text):
        char=text[i]; pair=text[i:i+2]
        if state=='line':
            if char=='\n': out.append(char); state='normal'
            i+=1; continue
        if state=='block':
            if pair=='*/': state='normal'; i+=2; continue
            if char=='\n': out.append(char)
            i+=1; continue
        if state=='quoted':
            out.append(char)
            if char=='\\' and quote!='`' and i+1<len(text): out.append(text[i+1]); i+=2; continue
            if char==quote: state='normal'
            i+=1; continue
        if pair=='//': state='line'; i+=2; continue
        if pair=='/*': state='block'; i+=2; continue
        out.append(char)
        if char in ['"',"'",'`']: state='quoted'; quote=char
        i+=1
    return ''.join(out)

def effective(text, extension):
    text=text.replace('\r\n','\n').replace('\r','\n')
    if extension in {'.go','.c','.h'}:
        lines=clean_c(text).splitlines()
    elif extension=='.py':
        lines=text.splitlines(); excluded=set()
        tree=ast.parse(text)
        for node in ast.walk(tree):
            body=getattr(node,'body',None)
            if isinstance(body,list) and body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
                excluded.update(range(body[0].lineno-1,body[0].end_lineno))
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type==tokenize.COMMENT:
                row=token.start[0]-1; lines[row]=lines[row][:token.start[1]]
        lines=['' if i in excluded else line for i,line in enumerate(lines)]
    else:
        lines=['' if line.lstrip().startswith('#') else line for line in text.splitlines()]
    return sum(bool(line.strip()) for line in lines)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--platform-source',type=Path)
    args=parser.parse_args()
    root=args.root.resolve(); rows=[]; seen=set()
    excluded={'tests','testcases','acceptance','validation','examples','docs','licenses','dist','bin','containerd-bin','__pycache__','.git'}
    for label,directory in [('monitor-core',root),('platform',args.platform_source)]:
        if directory is None: continue
        directory=directory.resolve()
        for path in sorted(directory.rglob('*')):
            if not path.is_file(): continue
            relative=path.relative_to(directory)
            if path.suffix not in {'.py','.go','.c','.h','.sh'} or any(p in excluded for p in relative.parts): continue
            if path.name.endswith('_test.go') or path.name.startswith(('test_','smoke_test')) or path.name in {'benchmark.c','count_code.py'}: continue
            data=path.read_bytes(); digest=hashlib.sha256(data).hexdigest()
            if digest in seen: continue
            seen.add(digest)
            text=data.decode('utf-8-sig')
            group='third-party' if label=='monitor-core' and relative.parts[0]=='third_party' else 'project'
            rows.append({'path':label+'/'+relative.as_posix(),'classification':group,'physicalLines':len(text.splitlines()),'effectiveLines':effective(text,path.suffix),'sha256':digest})
    summary={}
    for group in ['project','third-party']:
        selected=[row for row in rows if row['classification']==group]
        summary[group]={'files':len(selected),'physicalLines':sum(row['physicalLines'] for row in selected),'effectiveLines':sum(row['effectiveLines'] for row in selected)}
    total=sum(item['effectiveLines'] for item in summary.values())
    result={'scope':'容器化管理交付版的目标功能程序源码，包含项目安装部署、迁移、管理、扩缩容和监控程序，以及实际复用的第三方监控源码。',
            'platformBoundary':'Kubernetes、containerd、runc、CRIU、Open MPI、MPI Operator 等作为外部基础环境另列。Pod 内存采集、扩缩容决策和本次六项调度用例使用项目实现。',
            'method':'按相同口径统计有效代码行，去空行、注释及Python文档字符串；去重；排除测试、验证、样例、文档、二进制、统计工具；声明式配置单列不计入本程序源码比例。',
            'ownership':'既有安装脚本和本项目组件均按项目自研归属统计，不再用版本新增/修改量代替自研量。third_party中的裁剪和适配代码全部保守计入第三方复用。',
            'deploymentStatus':'新组件已经完成本机编译、单元测试、原控制器HTTP联调与Helm模板渲染；RISC-V交叉编译完成，尚未在RISC-V集群实机部署验收。本结果不替代旧发布包当前部署统计，也不自动等同开源社区贡献比例。',
            'failureComponent':'失效扰动抑制组件尚未集成，本次不统计。',
            'summary':summary,'totalEffectiveLines':total,'projectRatioPercent':round(100*summary['project']['effectiveLines']/total,4) if total else None,'files':rows}
    (root/'CODE-STATISTICS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (root/'CODE-STATISTICS.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps({key:result[key] for key in ['summary','totalEffectiveLines','projectRatioPercent']},indent=2))

if __name__=='__main__':
    main()
