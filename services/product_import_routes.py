"""Reviewable, owner-scoped and atomic file imports."""
import csv
import hashlib
import io
import secrets
import time
from flask import Blueprint, abort, flash, redirect, request, session, url_for, Response
from sqlalchemy import select, delete, text
from data import db_session
from data.product import ProductImport
from data.activity import ActivityEvent
from services import product_import as importer, product_service as service
from services.products import context_page, preferences_context
from services.storage import check_form
from services.auth_service import consume_limits

imports=Blueprint('product_import',__name__)
TTL=86400

def register_imports(app,subscription_required):
    @imports.before_request
    def bound_body():
        request.max_content_length=6*1024*1024 if request.endpoint=='product_import.upload' else 2*1024*1024
        request.max_form_memory_size=2*1024*1024

    def render(template,**kwargs):
        return context_page(template,import_labels=importer.LABELS,unit_options={k:v[0] for k,v in service.UNITS.items()},workspace_back_url=url_for('products.create'),**kwargs)

    @imports.route('/home/fridges/import',methods=['GET','POST'])
    @subscription_required
    def upload():
        pref=preferences_context();places=pref['product_storages']
        selected=request.form.get('storage_id') if request.method=='POST' else request.args.get('storage_id')
        selected=str(selected or pref['product_default_storage'] or (places[0].id if places else ''))
        error='';status=200
        if request.method=='POST':
            check_form()
            if selected not in {str(p.id) for p in places}:error='Выберите место хранения.'
            file=request.files.get('document')
            if not file or not file.filename:error='Выберите файл.'
            if not error:
                retry=consume_limits([('product-import-minute',str(session['user_id']),5,60),('product-import-hour',str(session['user_id']),30,3600)])
                if retry:return render('rate_limit.html',retry_after=retry),429,{'Retry-After':str(retry)}
                try:
                    rows=importer.read_file(file.filename,file.stream.read(importer.MAX_BYTES+1))
                    names={service.clean(p.name).casefold():str(p.id) for p in places}
                    ids={str(p.id) for p in places};warnings=[]
                    for row in rows:
                        raw=row.get('storage_id','')
                        if not raw:row['storage_id']=selected
                        elif raw in ids:row['storage_id']=raw
                        elif service.clean(raw).casefold() in names:row['storage_id']=names[service.clean(raw).casefold()]
                        else:row['storage_id']='';warnings.append('Некоторые места из файла не найдены. Выберите их перед сохранением.')
                        row['selected']=True
                    with db_session.create_session() as ses:
                        ses.execute(text('BEGIN IMMEDIATE'))
                        ses.execute(delete(ProductImport).where(ProductImport.owner_id==session['user_id'],ProductImport.status=='draft',ProductImport.created_at<time.time()-TTL))
                        drafts=list(ses.scalars(select(ProductImport).where(ProductImport.owner_id==session['user_id'],ProductImport.status=='draft').order_by(ProductImport.created_at)))
                        if len(drafts)>=5:raise importer.ImportProblem('У вас уже 5 незавершённых импортов. Завершите или отмените предыдущий импорт.')
                        item=ProductImport(id=secrets.token_hex(16),owner_id=session['user_id'],filename=file.filename.replace('\\','/').rsplit('/',1)[-1][:160],rows=rows,warnings=list(dict.fromkeys(warnings)))
                        ses.add(item);ses.flush();key=item.id
                        ses.add(ActivityEvent(owner_id=session['user_id'],category='products',action='import_preview',after=dict(filename=item.filename,count=len(rows))))
                        ses.commit()
                    return redirect(url_for('product_import.review',import_id=key))
                except importer.ImportProblem as exc:error=str(exc)
            status=422
        with db_session.create_session() as ses:
            drafts=list(ses.scalars(select(ProductImport).where(ProductImport.owner_id==session['user_id'],ProductImport.status=='draft',ProductImport.created_at>=time.time()-TTL).order_by(ProductImport.created_at.desc())))
        return render('product_import_upload.html',selected_storage=selected,import_error=error,drafts=drafts),status

    @imports.route('/products/import-template/<format>')
    @subscription_required
    def template(format):
        if format not in ['csv','xlsx','txt','json']:abort(404)
        table=[['Название','Марка','Количество','Единица','Годен до','Цена партии','Место хранения','Состав','Основание КБЖУ','Калории','Белки','Жиры','Углеводы'],
               ['Молоко','',900,'мл','','','','молоко нормализованное','На 100 мл',45,3,1.5,4.7],['Бананы','',3,'шт.','','','','','','','','','']]
        if format=='xlsx':
            from openpyxl import Workbook
            book=Workbook();sheet=book.active;sheet.title='Продукты'
            for row in table:sheet.append(row)
            sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
            for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=22
            out=io.BytesIO();book.save(out);body=out.getvalue();mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif format=='csv':
            out=io.StringIO();csv.writer(out,delimiter=';').writerows(table);body=out.getvalue().encode('utf-8-sig');mime='text/csv; charset=utf-8'
        elif format=='txt':
            body='Название: Молоко\nКоличество: 900\nЕдиница: мл\n\nНазвание: Бананы\nКоличество: 3\nЕдиница: шт.\n'.encode('utf-8');mime='text/plain; charset=utf-8'
        else:
            import json
            body=json.dumps([dict(name='Молоко',quantity=900,unit='ml'),dict(name='Бананы',quantity=3,unit='pcs')],ensure_ascii=False,indent=2).encode();mime='application/json'
        return Response(body,content_type=mime,headers={'Content-Disposition':f'attachment; filename="smartfridge-products.{format}"','X-Content-Type-Options':'nosniff'})

    @imports.route('/products/import/<import_id>',methods=['GET','POST'])
    @subscription_required
    def review(import_id):
        if len(import_id)!=32:abort(404)
        pref=preferences_context();place_ids={str(p.id) for p in pref['product_storages']}
        status=200;message=''
        with db_session.create_session() as ses:
            if request.method=='POST':
                check_form();ses.execute(text('BEGIN IMMEDIATE'))
            item=ses.scalar(select(ProductImport).where(ProductImport.id==import_id,ProductImport.owner_id==session['user_id']))
            if not item:abort(404)
            if item.status=='committed':return render('product_import_done.html',item=item)
            if item.status=='canceled':abort(410,description='Этот импорт отменён.')
            if item.created_at<time.time()-TTL:abort(410,description='Предпросмотр истёк. Загрузите файл заново.')
            rows=[dict(row) for row in item.rows]
            errors={}
            if request.method=='POST':
                if request.form.get('revision')!=str(item.revision):
                    message='Этот предпросмотр изменён в другом окне. Показаны актуальные данные. Проверьте их перед сохранением.';status=409
                elif request.form.get('action')=='cancel':
                    ses.add(ActivityEvent(owner_id=session['user_id'],category='products',action='import_canceled',before=dict(filename=item.filename,count=len(item.rows))))
                    item.status='canceled';item.rows=[];ses.commit();flash('Импорт отменён. Продукты не добавлены.','success');return redirect(url_for('product_import.upload'))
                elif request.form.get('action') in ['check','save']:
                    rows=[]
                    for i in range(len(item.rows)):
                        row={key:request.form.get(f'row.{i}.{key}','').strip() for key in service.FIELDS if key!='catalog_source'}
                        row.update(storage_id=request.form.get(f'row.{i}.storage_id',''),selected=request.form.get(f'row.{i}.selected')=='yes')
                        rows.append(row)
                    item.rows=rows;item.revision+=1
                else:abort(400)
            validated=[]
            for i,row in enumerate(rows):
                values,parsed,problems=service.validate(row)
                if row.get('storage_id') not in place_ids:problems['storage_id']='Выберите существующее место хранения.'
                errors[i]=problems
                if row.get('selected'):validated.append((i,row,values,parsed,problems))
            if request.method=='POST' and status!=409:
                if not validated:message='Выберите хотя бы один продукт.';status=422
                elif any(entry[4] for entry in validated):message='Исправьте отмеченные поля или снимите выбор с этих продуктов.';status=422
                elif request.form.get('action')=='save':
                    result=[]
                    try:
                        for i,row,values,parsed,_ in validated:
                            nonce=hashlib.sha256(f'{item.id}:{i}'.encode()).hexdigest()[:48]
                            group_id,_=service.save_in_session(ses,session['user_id'],int(row['storage_id']),nonce,values,parsed,source='import',source_note=item.filename)
                            result.append({'group_id':group_id,'name':values['name']})
                    except (service.StorageMissing,service.ProductConflict):
                        ses.rollback();abort(409,description='Данные изменились. Обновите предпросмотр и повторите сохранение.')
                    item.status='committed';item.result={'count':len(result),'products':result};item.rows=[];ses.commit()
                    return redirect(url_for('product_import.review',import_id=item.id))
                else:message='Данные проверены. Можно добавить выбранные продукты.'
                ses.commit()
            return render('product_import_review.html',item=item,rows=rows,row_errors=errors,import_error=message,selected_count=sum(bool(r.get('selected')) for r in rows)),status

    app.register_blueprint(imports)
