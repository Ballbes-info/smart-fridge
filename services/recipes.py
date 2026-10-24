"""Offline recipe library and owner-scoped ingredient/stock matching."""
import re
import sqlite3
from flask import session
from flask import Blueprint,abort,request,render_template,url_for
from services import recipe_catalog as catalog, recipe_matching as matching
from services.workspace import SECTIONS

recipes=Blueprint('recipes',__name__)


def register_recipes(app,subscription_required):
    @recipes.route('/home/recipes/catalog')
    @subscription_required
    def index():
        data=catalog.browse(request.args)
        params={key:value for key,value in data['filters'].items() if value}
        return render_template('recipe_list.html',active_section='recipes',section=SECTIONS['recipes'],feature=SECTIONS['recipes']['functions'][0],
            workspace_back_url=url_for('workspace.home'),previous_url=url_for('recipes.index',page=data['page']-1,**params),
            next_url=url_for('recipes.index',page=data['page']+1,**params),**data)

    @recipes.route('/home/recipes/available')
    @subscription_required
    def available():
        try:
            data=matching.browse(session['user_id'],request.args)
        except (OSError,sqlite3.Error,catalog.CatalogError):
            return render_template('recipe_match_error.html',active_section='recipes',section=SECTIONS['recipes'],
                feature=SECTIONS['recipes']['functions'][1],workspace_back_url=url_for('recipes.index')),503
        params={key:value for key,value in data['filters'].items() if value and key!='urgent'}
        if data['filters']['urgent']:params['urgent']='1'
        params.update(storage=data['snapshot']['selected'])
        if data['snapshot']['dated']:params['dated']='1'
        for key in data['snapshot']['pantry']:params['pantry_'+key]='1'
        for recipe in data['recipes']:
            recipe['detail_params']=dict(params,**{'from':'stock'})
            if not recipe['servings']:recipe['detail_params'].pop('servings',None)
        return render_template('recipe_available.html',active_section='recipes',section=SECTIONS['recipes'],
            feature=SECTIONS['recipes']['functions'][1],workspace_back_url=url_for('workspace.home'),pantry_options=matching.PANTRY,
            match_params=params,previous_url=url_for('recipes.available',page=data['page']-1,**params),
            next_url=url_for('recipes.available',page=data['page']+1,**params),**data),422 if data['filter_error'] else 200

    @recipes.route('/home/recipes/item/<recipe_key>')
    @subscription_required
    def detail(recipe_key):
        if not re.fullmatch(r'[a-f0-9]{24}',recipe_key):abort(404)
        recipe=catalog.detail(recipe_key,request.args.get('servings'))
        if recipe is None:abort(404)
        snapshot=matching.stock_snapshot(session['user_id'],request.args)
        assessment=matching.recipe_assessment(recipe,snapshot)
        context={key:request.args[key][:120] for key in ['storage','dated','pantry_water','pantry_salt','pantry_pepper','q','category','time','availability','sort','urgent','page'] if key in request.args}
        from_stock=request.args.get('from')=='stock'
        return render_template('recipe_detail.html',active_section='recipes',section=SECTIONS['recipes'],feature=SECTIONS['recipes']['functions'][0],
            workspace_back_url=url_for('recipes.available',**(context|({'servings':str(recipe['chosen_servings'])} if recipe['chosen_servings'] else {}))) if from_stock else url_for('recipes.index'),
            recipe=recipe,assessment=assessment,snapshot=snapshot,stock_context=context,from_stock=from_stock),422 if recipe['portion_error'] else 200

    app.register_blueprint(recipes)
