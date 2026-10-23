"""Shared, offline recipe library for every active subscription. No AI or stock writes."""
import re
from flask import Blueprint,abort,request,render_template,url_for
from services import recipe_catalog as catalog
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

    @recipes.route('/home/recipes/item/<recipe_key>')
    @subscription_required
    def detail(recipe_key):
        if not re.fullmatch(r'[a-f0-9]{24}',recipe_key):abort(404)
        recipe=catalog.detail(recipe_key,request.args.get('servings'))
        if recipe is None:abort(404)
        return render_template('recipe_detail.html',active_section='recipes',section=SECTIONS['recipes'],feature=SECTIONS['recipes']['functions'][0],
            workspace_back_url=url_for('recipes.index'),recipe=recipe),422 if recipe['portion_error'] else 200

    app.register_blueprint(recipes)
