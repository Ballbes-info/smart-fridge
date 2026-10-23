"""Account history stays accessible even when a subscription has expired."""
from flask import Blueprint,request,session,render_template,url_for,Response,abort
from sqlalchemy import select,func
from data import db_session
from services import activity_history as history

activity=Blueprint('activity',__name__)


def register_activity(app,login_required):
    def load(export=False):
        query_args=request.args.to_dict()
        if request.endpoint=='products.history':query_args.setdefault('category','products')
        try:values,source,conditions=history.filtered(query_args,session['user_id'])
        except ValueError as exc:abort(400,description=str(exc))
        query=select(source).where(*conditions).order_by(source.c.created_at.desc(),source.c.source,source.c.id.desc())
        with db_session.create_session() as ses:
            total=ses.scalar(select(func.count()).select_from(source).where(*conditions));pages=max(1,(total+29)//30)
            try:page=max(1,min(pages,int(request.args.get('page','1'))))
            except ValueError:page=1
            if export:
                if total>5000:abort(413,description='За один раз можно экспортировать до 5000 событий. Сузьте период или выберите раздел.')
                items=list(ses.execute(query).all())
            else:items=list(ses.execute(query.offset((page-1)*30).limit(30)).all())
            events=history.hydrate(ses,items,session['user_id'])
        return values,events,total,page,pages

    @activity.route('/history')
    @login_required
    def index():
        values,events,total,page,pages=load()
        args={k:v for k,v in values.items() if v}
        return render_template('activity_history.html',active_section='settings',workspace_back_url=url_for('workspace.settings'),
            history_filters=values,categories=history.CATEGORIES,history_actions=history.ACTIONS,history_statuses=history.STATUSES,events=events,total=total,page=page,pages=pages,
            export_xlsx=url_for('activity.download',format='xlsx',**args),export_csv=url_for('activity.download',format='csv',**args),
            previous_url=url_for('activity.index',page=page-1,**args),next_url=url_for('activity.index',page=page+1,**args))

    @activity.route('/history/export/<format>')
    @login_required
    def download(format):
        if format not in ['csv','xlsx']:abort(404)
        values,events,total,_,_=load(export=True)
        body,mime=history.export(events,format)
        from services.activity_audit import record
        record(session['user_id'],'history_exported',after=dict(format=format.upper(),events=total))
        return Response(body,content_type=mime,headers={'Content-Disposition':f'attachment; filename="smartfridge-history.{format}"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    app.register_blueprint(activity)
