"""Web-only assistant, private job polling and premium companion controls."""
import secrets
from pathlib import Path
from urllib.parse import urlsplit,unquote
from flask import Blueprint,render_template,request,session,jsonify,abort,redirect,url_for,flash
from data import db_session
from data.assistant import AssistantExchange,AssistantContext
from services import assistant_service as ai
from services.storage import check_form

assistant=Blueprint('assistant',__name__)


def register_assistant(app,login_required,subscription_required):
    @assistant.app_context_processor
    def companion_context():
        owner=session.get('user_id')
        if not owner:return {}
        q=ai.quota(owner);allowed=q['allowed'];prefs=ai.preferences(owner);saved=prefs['enabled']
        pet=allowed and saved and not session.get('assistant_pet_snoozed')
        job=ai.pending(owner) if pet else None
        last=job or (ai.latest(owner) if pet else None)
        recipe_key=(request.view_args or {}).get('recipe_key','') if request.endpoint=='recipes.detail' else ''
        return dict(assistant_pet_rive=(Path(app.static_folder)/'pets/bunny/character.riv').is_file(),assistant_pet_options=prefs,assistant_pet_scope=str(owner),assistant_pet_quota=q,assistant_pet_recipe=dict(key=recipe_key,servings=request.args.get('servings','')) if recipe_key else None,assistant_pet_last=dict(id=last.id,mode=last.mode,status=last.status) if last else None,assistant_pet_pending=dict(id=job.id,mode=job.mode) if job else None,assistant_premium=allowed,assistant_pet_saved=saved,assistant_pet=pet,assistant_csrf=session.setdefault('storage_csrf',secrets.token_hex(32)))

    @assistant.after_request
    def private(response):
        if request.path.startswith(('/assistant/send','/assistant/jobs/','/assistant/history/')):
            if response.status_code in (301,302,303,307,308):
                response=jsonify(error='Сессия или подписка закончилась. Обновите страницу.',code='access');response.status_code=403
            elif response.status_code in (400,401,403,404) and not response.is_json:
                descriptions={400:'Форма устарела. Обновите страницу.',401:'Войдите в аккаунт заново.',403:'Нет доступа к помощнику. Проверьте подписку.',404:'Разговор недоступен или уже удалён.'}
                code=response.status_code;response=jsonify(error=descriptions[code],code='access' if code in (401,403) else 'form');response.status_code=code
        response.headers['Cache-Control']='no-store';return response

    @assistant.route('/home/assistant')
    @assistant.route('/home/assistant/<mode>')
    @subscription_required
    def index(mode='chat'):
        if mode=='voice':return redirect(url_for('assistant.index',mode='guide'))
        if mode not in ai.MODES:abort(404)
        q=ai.quota(session['user_id'])
        if mode!='guide' and not q['allowed']:
            from services.workspace import REQUIREMENTS
            return render_template('workspace_locked.html',active_section='assistant',access_title=ai.MODES[mode],requirement=REQUIREMENTS['premium']),403
        page=ai.history_page(session['user_id'],mode) if mode!='guide' else dict(rows=[],before=None)
        rows=page['rows'];active=ai.pending(session['user_id'])
        pending=active.id if active and active.mode==mode else None
        recipe=None;context_error=''
        if mode=='cooking' and request.args.get('recipe_key'):
            try:recipe=ai.recipe_context(session['user_id'],request.args['recipe_key'],request.args.get('servings'),request.args.get('step'))
            except ValueError as exc:context_error=str(exc)
        if pending and not recipe:
            row=next((r for r in rows if r.id==pending),None)
            if row:recipe=getattr(row,'context_info',{}).get('recipe')
        recipe_return=None
        if recipe:
            recipe_return=url_for('recipes.detail',recipe_key=recipe['key'],servings=recipe.get('servings') or '',_anchor='cooking')
            raw=request.args.get('recipe_return','')
            if raw and len(raw)<=3000:
                try:
                    decoded=unquote(raw);parts=urlsplit(decoded)
                    if not parts.scheme and not parts.netloc and parts.path==url_for('recipes.detail',recipe_key=recipe['key']) and '\\' not in decoded and not any(ord(c)<32 for c in decoded):recipe_return=raw
                except ValueError:pass
        return render_template('assistant.html',active_section='assistant',mode=mode,feature=dict(slug=mode,name=ai.MODES[mode]),modes=ai.MODES,guide=ai.GUIDE,rows=rows,pending=pending,other_pending=active.mode if active and active.mode!=mode else None,quota=q,nonce=secrets.token_hex(16),older=page['before'],recipe_context=recipe,context_error=context_error,recipe_return=recipe_return,workspace_back_url=recipe_return or url_for('workspace.home'))

    @assistant.post('/assistant/send')
    @subscription_required
    def send():
        check_form()
        try:ident=ai.start(session['user_id'],request.form.get('mode'),request.form.get('question',''),request.form.get('nonce'),request.form.get('with_stock')=='1',request.form.get('recipe_key',''),request.form.get('servings'),request.form.get('step'))
        except ValueError as exc:return jsonify(error=str(exc)),422
        return jsonify(id=ident),202

    @assistant.get('/assistant/jobs/<ident>')
    @subscription_required
    def job(ident):
        with db_session.create_session() as s:
            if not ai.quota(session['user_id'],s)['allowed']:abort(403)
            row=s.get(AssistantExchange,ident)
            if not row or row.owner_id!=session['user_id'] or row.hidden:abort(404)
            context=s.get(AssistantContext,row.id);row.context_info=context.data if context else {}
            return jsonify(**ai.serialize(row),remaining=ai.quota(session['user_id'],s)['remaining'])

    @assistant.get('/assistant/history/<mode>')
    @subscription_required
    def history(mode):
        if mode not in ('chat','cooking'):abort(404)
        if not ai.quota(session['user_id'])['allowed']:abort(403)
        try:page=ai.history_page(session['user_id'],mode,request.args.get('before'))
        except ValueError:abort(404)
        return jsonify(rows=[ai.serialize(row) for row in page['rows']],before=page['before'])

    @assistant.post('/assistant/clear')
    @subscription_required
    def clear():
        check_form();mode=request.form.get('mode')
        if mode not in ('chat','cooking'):abort(400)
        try:ai.clear(session['user_id'],mode);flash('История разговора удалена.','success')
        except ValueError as exc:flash(str(exc),'error')
        return redirect(url_for('assistant.index',mode=mode))

    @assistant.post('/settings/companion')
    @login_required
    def pet():
        check_form()
        try:
            ai.set_pet(session['user_id'],request.form.get('enabled')=='1',request.form.get('roaming')=='1' if request.form.get('pet_preferences')=='1' else None,request.form.get('hints')=='1' if request.form.get('pet_preferences')=='1' else None);session.pop('assistant_pet_snoozed',None);flash('Настройка питомца сохранена.','success')
        except ValueError as exc:flash(str(exc),'error')
        return redirect(url_for('workspace.settings')+'#assistant-pet-settings')

    @assistant.post('/assistant/pet/hide')
    @login_required
    def hide_pet():
        check_form();session['assistant_pet_snoozed']=True
        return jsonify(ok=True)

    app.register_blueprint(assistant)
