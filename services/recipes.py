"""Complete recipe workspace: browse/match, bookmarks, nutrition, cooking and AI."""
import re
import hmac
from urllib.parse import urlsplit,unquote
import secrets
import time
import threading
import sqlite3
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from flask import Blueprint,abort,request,render_template,url_for,session,jsonify,redirect,flash,Response
from data import db_session
from data.recipe import RecipeGeneration
from services import recipe_catalog as catalog,recipe_matching as matching,recipe_jobs as jobs,recipe_library as library,recipe_generation as generation
from services.workspace import SECTIONS,access_state
from services.storage import check_form

recipes=Blueprint('recipes',__name__)


def feature(slug):return next(f for f in SECTIONS['recipes']['functions'] if f['slug']==slug)
def common(slug='catalog'):
    session.setdefault('storage_csrf',secrets.token_hex(32))
    return dict(active_section='recipes',section=SECTIONS['recipes'],feature=feature(slug),recipe_csrf=session['storage_csrf'],workspace_back_url=url_for('workspace.home'))


def safe_return(value):
    if not value or len(value)>3000:return None
    try:
        decoded=unquote(value);parts=urlsplit(decoded)
    except ValueError:return None
    allowed={'/home/recipes','/home/recipes/catalog','/home/recipes/available','/home/recipes/saved','/home/recipes/nutrition','/home/recipes/generate'}
    if parts.scheme or parts.netloc or parts.path not in allowed or '\\' in decoded or any(ord(c)<32 for c in decoded):return None
    # A completed generation job redirects straight back to its recipe: never use it as Back.
    if parts.path=='/home/recipes/generate':return url_for('recipes.generate')
    return value


def list_origins(data,endpoint,params):
    origin=url_for(endpoint,page=data['page'],**params)
    for recipe in data['recipes']:
        recipe['detail_params']=dict(recipe.get('detail_params',{}),return_to=origin)


def match_links(data):
    params={k:v for k,v in data['filters'].items() if v and k!='urgent'}
    if data['filters']['urgent']:params['urgent']='1'
    params['storage']=data['snapshot']['selected']
    if data['snapshot']['dated']:params['dated']='1'
    for k in data['snapshot']['pantry']:params['pantry_'+k]='1'
    for r in data['recipes']:
        r['detail_params']=dict(params,**{'from':'stock'})
        if not r['servings']:r['detail_params'].pop('servings',None)
    list_origins(data,'recipes.available',params)
    return dict(match_params=params,previous_url=url_for('recipes.available',page=data['page']-1,**params),next_url=url_for('recipes.available',page=data['page']+1,**params))


def match_job(args):
    job=jobs.start(session['user_id'],args);data=None
    if job['status']=='ready':
        try:data=jobs.page(job,args)
        except (OSError,sqlite3.Error):
            jobs.discard(job);job=jobs.start(session['user_id'],args)
    return job,data


def premium():
    state=access_state('recipes','generate')
    if state['locked']:return render_template('workspace_locked.html',access_title='Создать с ИИ',requirement=state['requirement'],**common('generate')),403


def register_recipes(app,subscription_required):
    @recipes.route('/home/recipes/catalog')
    @subscription_required
    def index():
        data=catalog.browse(request.args);params={k:v for k,v in data['filters'].items() if v}
        list_origins(data,'recipes.index',params)
        return render_template('recipe_list.html',**common(),previous_url=url_for('recipes.index',page=data['page']-1,**params),next_url=url_for('recipes.index',page=data['page']+1,**params),**data)

    @recipes.route('/home/recipes/available')
    @subscription_required
    def available():
        data=matching.context(session['user_id'],request.args);job=None
        if data['catalog']['count'] and data['snapshot']['valid'] and not data['filter_error'] and (data['snapshot']['batches'] or data['snapshot']['pantry']):
            job,cached=match_job(request.args.to_dict())
            status='ready' if cached is not None else ('pending' if job['status']=='ready' else job['status'])
            if cached is not None:data=cached
            partial=job.get('partial')
            if status=='pending' and partial and request.args.get('page','1')=='1' and all(data['filters'][k]==partial['filters'][k] for k in ['sort','availability','urgent']):data=partial
            job={'status':status}
        return render_template('recipe_available.html',**common(),pantry_options=matching.PANTRY,job=job,
            poll_url=url_for('recipes.match_status',**{k:v for k,v in request.args.items() if k!='retry'}),**match_links(data),**data),422 if data['filter_error'] else 200

    @recipes.route('/recipes/matching/status')
    @subscription_required
    def match_status():
        job,data=match_job(request.args.to_dict());status='ready' if data is not None else ('pending' if job['status']=='ready' else job['status'])
        if data is not None:
            return jsonify(status='ready',total=data['total'],html=render_template('recipe_match_results.html',**data,**match_links(data))),200,{'Cache-Control':'no-store'}
        partial=job.get('partial')
        if status=='pending' and partial and request.args.get('page','1')=='1':
            data=partial
            if all(matching.filters(request.args)[0][k]==data['filters'][k] for k in ['sort','availability','urgent']):
                return jsonify(status='pending',partial=True,done=job['done'],total=job['total'],html=render_template('recipe_match_results.html',**data,**match_links(data))),200,{'Cache-Control':'no-store'}
        return jsonify(status=status,phase=job.get('phase'),done=job.get('done',0),total=job.get('total',0),message=job.get('message','')),200,{'Cache-Control':'no-store'}

    @recipes.route('/home/recipes/saved')
    @subscription_required
    def saved():
        data=library.favorites(session['user_id'],request.args);params=dict(q=data['q'],kind=data['kind'])
        list_origins(data,'recipes.saved',params)
        return render_template('recipe_saved.html',**common('saved'),previous_url=url_for('recipes.saved',page=data['page']-1,**params),next_url=url_for('recipes.saved',page=data['page']+1,**params),**data)

    @recipes.route('/home/recipes/nutrition')
    @subscription_required
    def nutrition():
        data=catalog.browse(dict(request.args,content='complete'))
        for r in data['recipes']:r['nutrition_link']=True
        params={k:v for k,v in data['filters'].items() if v}
        list_origins(data,'recipes.nutrition',params)
        return render_template('recipe_nutrition_list.html',**common('nutrition'),**data,
            previous_url=url_for('recipes.nutrition',page=data['page']-1,**params),next_url=url_for('recipes.nutrition',page=data['page']+1,**params))

    @recipes.route('/home/recipes/generate',methods=['GET','POST'])
    @subscription_required
    def generate():
        denied=premium()
        if denied:return denied
        error='';job_id=request.args.get('job','')
        if request.method=='POST':
            expected=session.get('storage_csrf','');actual=request.form.get('csrf_token','')
            if not expected or not hmac.compare_digest(expected.encode(),actual.encode()):
                error='Форма обновлена. Проверьте пожелания и нажмите «Создать рецепт» ещё раз.'
            else:
                try:job_id=generation.start(session['user_id'],request.form)
                except ValueError as exc:error=str(exc)
                else:return redirect(url_for('recipes.generate',job=job_id))
        if not job_id:
            with db_session.create_session() as s:
                pending=s.scalar(select(RecipeGeneration).where(RecipeGeneration.owner_id==session['user_id'],RecipeGeneration.status=='pending',RecipeGeneration.created_at>time.time()-600).order_by(RecipeGeneration.created_at.desc()))
                if pending:job_id=pending.id
        job=None
        if re.fullmatch('[a-f0-9]{32}',job_id):
            with db_session.create_session() as s:
                row=s.get(RecipeGeneration,job_id)
                if row and row.owner_id==session['user_id']:
                    if row.status=='used':return redirect(url_for('recipes.detail',recipe_key=row.recipe_key,return_to=url_for('recipes.generate')))
                    if row.status=='pending' and row.created_at<time.time()-600:row.status='failed';row.error='Запрос прерван. Повторите создание.';s.commit()
                    job=dict(status=row.status,error=row.error,id=row.id)
        return render_template('recipe_generate.html',**common('generate'),quota=generation.quota(session['user_id']),error=error,job=job,
            request_id=secrets.token_hex(16),values=request.form if error else {}),422 if error else 200

    @recipes.route('/recipes/generation/<job_id>')
    @subscription_required
    def generation_status(job_id):
        denied=premium()
        if denied:abort(403)
        with db_session.create_session() as s:
            row=s.get(RecipeGeneration,job_id)
            if not row or row.owner_id!=session['user_id']:abort(404)
            if row.status=='pending' and row.created_at<time.time()-600:row.status='failed';row.error='Запрос прерван. Повторите создание.';s.commit()
            return jsonify(status=row.status,error=row.error,url=url_for('recipes.detail',recipe_key=row.recipe_key,return_to=url_for('recipes.generate')) if row.status=='used' else None),200,{'Cache-Control':'no-store'}

    @recipes.route('/home/recipes/item/<recipe_key>',methods=['GET','POST'])
    @subscription_required
    def detail(recipe_key):
        if not re.fullmatch(r'[a-f0-9]{24}',recipe_key):abort(404)
        recipe=library.recipe(session['user_id'],recipe_key,request.args.get('servings'))
        if recipe is None:abort(404)
        if request.method=='POST':
            ajax=request.accept_mimetypes.best=='application/json'
            if ajax:
                expected=session.get('storage_csrf','');actual=request.form.get('csrf_token','')
                if not expected or not hmac.compare_digest(expected.encode(),actual.encode()):
                    session.setdefault('storage_csrf',secrets.token_hex(32))
                    return jsonify(ok=False,message='Форма обновлена. Повторите действие.',csrf=session['storage_csrf']),409
            else:check_form()
            action=request.form.get('action')
            try:library.change(session['user_id'],recipe_key,recipe,action,request.form)
            except (ValueError,OperationalError) as exc:
                message=str(exc) if isinstance(exc,ValueError) else 'Не удалось сохранить: база занята. Повторите через несколько секунд.'
                if ajax:return jsonify(ok=False,message=message),422 if isinstance(exc,ValueError) else 503
                flash(message,'error')
            else:
                if ajax:
                    personal=library.state(session['user_id'],recipe_key)
                    payload=dict(ok=True,message='Сохранено',favorite=personal['favorite'],checked=personal['checked'])
                    if action in ['nutrition','nutrition_reset']:
                        payload['nutrition_html']=render_template('recipe_nutrition.html',recipe=recipe,personal=personal,
                            nutrition=library.nutrition(session['user_id'],recipe,personal['nutrition']),nutrient_names=library.NUTRIENTS,recipe_csrf=session['storage_csrf'])
                    return jsonify(payload)
                flash({'favorite':'Избранное обновлено','note':'Заметка сохранена','steps':'Шаги сохранены','nutrition':'Расчёт сохранён','nutrition_reset':'Уточнения сброшены','steps_reset':'Шаги сброшены'}[action],'success')
            return redirect(url_for('recipes.detail',recipe_key=recipe_key,**{k:v for k,v in request.args.items() if k in ['servings','from','storage','dated','pantry_water','pantry_salt','pantry_pepper','q','category','time','availability','sort','urgent','page','return_to']},_anchor='nutrition' if action in ['nutrition','nutrition_reset'] else 'cooking' if action in ['steps','steps_reset'] else 'recipe-personal'))
        snapshot=matching.stock_snapshot(session['user_id'],request.args);assessment=matching.recipe_assessment(recipe,snapshot)
        personal=library.state(session['user_id'],recipe_key)
        context={k:request.args[k][:120] for k in ['storage','dated','pantry_water','pantry_salt','pantry_pepper','q','category','time','availability','sort','urgent','page'] if k in request.args}
        from_stock=request.args.get('from')=='stock'
        ctx=common();ctx['workspace_back_url']=url_for('recipes.available',**(context|({'servings':str(recipe['chosen_servings'])} if recipe['chosen_servings'] else {}))) if from_stock else url_for('recipes.index')
        origin=safe_return(request.args.get('return_to'))
        if origin:
            ctx['workspace_back_url']=origin;context['return_to']=origin
        for source,slug in [('/home/recipes/saved','saved'),('/home/recipes/nutrition','nutrition'),('/home/recipes/generate','generate')]:
            if origin and urlsplit(origin).path==source:ctx['feature']=feature(slug)
        return render_template('recipe_detail.html',**ctx,recipe=recipe,assessment=assessment,snapshot=snapshot,stock_context=context,from_stock=from_stock,
            personal=personal,cooking_steps=library.cooking_steps(recipe),nutrition=library.nutrition(session['user_id'],recipe,personal['nutrition']),nutrient_names=library.NUTRIENTS),422 if recipe['portion_error'] else 200

    @recipes.route('/recipes/item/<recipe_key>/export')
    @subscription_required
    def export(recipe_key):
        r=library.recipe(session['user_id'],recipe_key,request.args.get('servings'))
        if r is None:abort(404)
        body=r['title']+'\n\nИнгредиенты:\n'+'\n'.join(i['name']+' — '+i['display'] for i in r['ingredients'])+'\n\nПриготовление:\n'+'\n'.join(str(n+1)+'. '+((step.get('title')+'\n') if step.get('title') else '')+step['text'] for n,step in enumerate(r['steps']))
        if r.get('source_url'):body+='\n\nИсточник: '+r['source_url']
        elif r.get('generated'):body+='\n\nСоздано GigaChat. Проверьте ингредиенты и готовность блюда.'
        return Response(body,content_type='text/plain; charset=utf-8',headers={'Content-Disposition':'attachment; filename="recipe.txt"','Cache-Control':'no-store'})

    app.register_blueprint(recipes)
    # Prepare small covering indexes before accepting requests, never halfway
    # through a match job (DDL changes the catalog file's cache signature).
    try:catalog.prepare_read_indexes()
    except (OSError,sqlite3.Error):app.logger.warning('Recipe browse indexes unavailable; using read-only pagination.')
    started=False;warm_lock=threading.Lock()
    @app.before_request
    def warm_recipes_once():
        nonlocal started
        if not started and request.endpoint!='static':
            with warm_lock:
                if not started:started=True;jobs.warmup()
