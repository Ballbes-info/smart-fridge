"""Nutrition pages and validated mutations; all records belong to the signed-in user."""
import csv
import io
import secrets
from datetime import timedelta
from flask import Blueprint, render_template, request, session, redirect, url_for, flash, abort, Response
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from data import db_session
from data.nutrition import NutritionEntry, NutritionMenu
from services import nutrition_service as ns, product_service as stock, recipe_catalog as catalog
from services.workspace import SECTIONS, access_state
from services.storage import check_form
from services.product_operations import OperationError

nutrition=Blueprint('nutrition',__name__)


def common(slug='diary'):
    session.setdefault('storage_csrf',secrets.token_hex(32))
    return dict(active_section='nutrition',section=SECTIONS['nutrition'],feature=next(f for f in SECTIONS['nutrition']['functions'] if f['slug']==slug),
                workspace_back_url=url_for('workspace.home'),nutrition_csrf=session['storage_csrf'],measure_token=secrets.token_hex(24),meals=ns.MEALS,nutrient_names=ns.NAMES,units=ns.UNITS,today=stock.today())


def selected_day(future=False):return ns.day_value(request.values.get('day',stock.today().isoformat()),future=future)


def menu_denied():
    state=access_state('nutrition','menu')
    if state['locked']:return render_template('workspace_locked.html',access_title='Персональное меню',requirement=state['requirement'],**common('menu')),403


def register_nutrition(app,subscription_required):
    @nutrition.route('/home/nutrition/diary')
    @subscription_required
    def diary():
        try:day=selected_day()
        except ValueError as exc:flash(str(exc),'error');return redirect(url_for('nutrition.diary'))
        return render_template('nutrition_diary.html',**common(),data=ns.summary(session['user_id'],day),day=day,
            previous=day-timedelta(days=1),following=day+timedelta(days=1),submission_key=secrets.token_hex(24))

    @nutrition.route('/home/nutrition/nutrients')
    @subscription_required
    def nutrients():
        try:day=selected_day()
        except ValueError as exc:flash(str(exc),'error');return redirect(url_for('nutrition.nutrients'))
        return render_template('nutrition_nutrients.html',**common('nutrients'),data=ns.summary(session['user_id'],day),day=day)

    @nutrition.route('/home/nutrition/goals',methods=['GET','POST'])
    @subscription_required
    def goals():
        values,revision=ns.profile(session['user_id']);error=''
        if request.method=='POST':
            check_form()
            try:ns.save_profile(session['user_id'],request.form)
            except ValueError as exc:error=str(exc);values=dict(request.form)
            else:flash('Цели сохранены','success');return redirect(url_for('nutrition.goals'))
        return render_template('nutrition_goals.html',**common('goals'),values=values,revision=revision,error=error,activities=ns.ACTIVITIES,goals=ns.GOALS),422 if error else 200

    @nutrition.route('/home/nutrition/add',methods=['GET','POST'])
    @nutrition.route('/home/nutrition/entry/<int:entry_id>',methods=['GET','POST'])
    @subscription_required
    def entry(entry_id=None):
        error='';row=None;source={};owner=session['user_id']
        kind=request.values.get('source_kind','manual');key=request.values.get('source_key','')
        if entry_id:
            with db_session.create_session() as s:row=s.get(NutritionEntry,entry_id)
            if not row or row.owner_id!=owner or row.deleted:abort(404)
            values=dict(name=row.name,day=str(row.day),meal=row.meal,quantity=row.quantity,unit=row.unit,note=row.note,
                        basis=row.source.get('basis','total'),basis_quantity=row.source.get('basis_quantity',''),**row.source.get('input_values',{k:row.nutrients.get(k) or '' for k in ns.NAMES}))
            source=row.source;kind=source.get('kind','manual');key=source.get('key','')
        elif request.method=='POST':
            values=dict(request.form)
        else:
            try:
                values,source=ns.source_values(owner,kind,key)
                day=selected_day();meal=request.args.get('meal','breakfast')
                values.update(day=str(day),meal=meal if meal in ns.MEALS else 'breakfast',note='')
                if kind=='recipe' and request.args.get('quantity'):values['quantity']=ns.shown(ns.number(request.args['quantity'],'Порции',.1,20))
            except ValueError as exc:
                flash(str(exc),'error');return redirect(url_for('nutrition.sources'))
            except OperationError:abort(404)
        if request.method=='POST':
            check_form()
            try:ns.save_entry(owner,request.form,entry_id)
            except ValueError as exc:error=str(exc);values=dict(request.form)
            except OperationError:abort(404)
            else:flash('Запись сохранена','success');return redirect(url_for('nutrition.diary',day=values['day']))
        ctx=common();ctx['workspace_back_url']=url_for('nutrition.diary',day=values['day'])
        return render_template('nutrition_entry.html',**ctx,values=values,error=error,row=row,source=source,source_kind=kind,source_key=key,submission_key=request.form.get('submission_key') or secrets.token_hex(24)),422 if error else 200

    @nutrition.route('/home/nutrition/sources')
    @subscription_required
    def sources():
        try:day=selected_day()
        except ValueError:day=stock.today()
        q=stock.clean(request.args.get('q',''))[:120];meal=request.args.get('meal','breakfast')
        if meal not in ns.MEALS:meal='breakfast'
        kind=request.args.get('kind','stock')
        recipes=ns.search_recipes(session['user_id'],q) if kind=='recipe' else []
        return render_template('nutrition_sources.html',**common(),day=day,q=q,meal=meal,kind=kind,products=ns.stock_options(session['user_id'],q) if kind!='recipe' else [],recipes=recipes)

    @nutrition.route('/nutrition/entry/<int:entry_id>/delete',methods=['POST'])
    @subscription_required
    def delete_entry(entry_id):
        check_form()
        if request.form.get('confirm')!='yes':abort(400)
        try:ns.remove_entry(session['user_id'],entry_id,request.form.get('revision'))
        except ValueError as exc:flash(str(exc),'error')
        else:flash('Запись удалена. Остатки продуктов не изменены.','success')
        return redirect(url_for('nutrition.diary',day=request.form.get('day','')))

    @nutrition.route('/nutrition/measure',methods=['POST'])
    @subscription_required
    def measure():
        check_form()
        try:ns.measure(session['user_id'],request.form)
        except ValueError as exc:flash(str(exc),'error')
        return redirect(url_for('nutrition.nutrients' if request.form.get('kind')=='weight' else 'nutrition.diary',day=request.form.get('day','')))

    @nutrition.route('/nutrition/measure/<int:measure_id>/delete',methods=['POST'])
    @subscription_required
    def delete_measure(measure_id):
        check_form()
        try:ns.remove_measure(session['user_id'],measure_id)
        except ValueError as exc:flash(str(exc),'error')
        return redirect(url_for('nutrition.nutrients',day=request.form.get('day','')))

    @nutrition.route('/home/nutrition/menu',methods=['GET','POST'])
    @subscription_required
    def menu():
        denied=menu_denied()
        if denied:return denied
        try:day=selected_day(future=True)
        except ValueError as exc:flash(str(exc),'error');return redirect(url_for('nutrition.menu'))
        if request.method=='POST':
            check_form()
            try:
                action=request.form.get('action')
                if action=='save':ns.save_menu(session['user_id'],request.form)
                elif action=='auto':
                    count=ns.auto_menu(session['user_id'],request.form);flash('Добавлено приёмов пищи: '+str(count)+'. Уже заполненные оставлены.','success')
                elif action=='delete':
                    with db_session.create_session() as s:
                        s.execute(text('BEGIN IMMEDIATE'));row=s.get(NutritionMenu,ns.ident(request.form.get('id')))
                        if not row or row.owner_id!=session['user_id']:abort(404)
                        if str(row.revision)!=request.form.get('revision'):raise ValueError('План уже изменён. Обновите страницу.')
                        ns.event(s,session['user_id'],'menu_delete',row.snapshot['title']);s.delete(row);s.commit()
                else:abort(400)
            except ValueError as exc:flash(str(exc),'error')
            return redirect(url_for('nutrition.menu',day=day))
        q=stock.clean(request.args.get('q',''))[:120];key=request.args.get('recipe_key','');picked=None
        if key:
            try:picked=ns.source_values(session['user_id'],'recipe',key)[0]
            except ValueError:flash('Рецепт больше недоступен. Выберите другой.','error')
        search=ns.search_recipes(session['user_id'],q) if q else []
        return render_template('nutrition_menu.html',**common('menu'),day=day,week=ns.menu_week(session['user_id'],day),q=q,recipes=search,picked=picked,recipe_key=key,goals=ns.profile(session['user_id'])[0])

    @nutrition.route('/nutrition/export')
    @subscription_required
    def export():
        try:day=selected_day()
        except ValueError:abort(400)
        data=ns.summary(session['user_id'],day);out=io.StringIO();out.write('\ufeff');writer=csv.writer(out,delimiter=';')
        writer.writerow(['Дата','Приём пищи','Название','Количество','Единица','Ккал','Белки, г','Жиры, г','Углеводы, г','Заметка'])
        def safe(v):
            v=str(v)
            return "'"+v if v.lstrip().startswith(('=','+','-','@')) else v
        for d in data['days']:
            for row in d['entries']:writer.writerow([str(row.day),ns.MEALS[row.meal],safe(row.name),row.quantity,ns.UNITS[row.unit],*[row.nutrients.get(k) if row.nutrients.get(k) is not None else '' for k in ns.NAMES],safe(row.note)])
        return Response(out.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="nutrition-week.csv"','Cache-Control':'no-store'})

    @nutrition.after_request
    def private(response):
        response.headers['Cache-Control']='no-store';return response

    @nutrition.errorhandler(OperationalError)
    def busy(exc):
        return render_template('nutrition_error.html',**common(),message='База временно занята. Повторите действие через несколько секунд.'),503

    app.register_blueprint(nutrition)
