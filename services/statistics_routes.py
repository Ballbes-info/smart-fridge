"""Published statistics destinations; filtered, privacy-scoped CSVs and no-cache responses."""
import csv
import io
from datetime import date,timedelta
from decimal import Decimal
from flask import Blueprint,render_template,request,session,url_for,Response,abort
from data import db_session
from services import statistics_service as stats,product_service as stock
from services.workspace import SECTIONS

statistics=Blueprint('statistics',__name__)
TABS={'inventory':'Запасы','usage':'Расход','waste':'Потери','savings':'Экономия'}


def register_statistics(app,subscription_required):
    @statistics.app_template_filter('stats_money')
    def money(metric):
        value=stats.cash(metric)
        return '—' if value is None else format(Decimal(value)/100,',.2f').replace(',',' ').replace('.',',').removesuffix(',00')+' ₽'

    @statistics.app_template_filter('stats_amount')
    def amount(row):
        if row.get('quantity') is None or row.get('dimension') not in stats.DIMENSIONS:return 'Не указано'
        return stock.quantity_text(row['quantity'],row['dimension'])

    def params(f,**extra):
        values={key:f[key] for key in ('period','scope','chart','storage','category','q') if f.get(key)}
        if f['period']=='custom':values.update(start=f['start'].isoformat(),end=f['end'].isoformat())
        values.update(extra);return values

    def common(tab,f):
        return dict(active_section='statistics',section=SECTIONS['statistics'],feature=next(item for item in SECTIONS['statistics']['functions'] if item['slug']==tab),
            tabs=TABS,tab=tab,presets=stats.PRESETS,categories=stock.CATEGORIES,dimensions=stats.DIMENSIONS,reasons=stats.REASONS,
            workspace_back_url=url_for('workspace.home'),today=stock.today(),filters=f,filter_params=params(f),money_filter=money,
            tab_urls={slug:url_for('statistics.page',tab=slug,**params(f)) for slug in TABS})

    def failure(tab,f,exc):
        context=common(tab,f)
        if exc.status!=403:
            with db_session.create_session() as ses:
                try:context['data']={'places':stats.locations(ses,session['user_id'],dict(f,storage=''))}
                except stats.ReportError:pass
        return render_template('statistics_error.html',**context,error=str(exc)),exc.status

    @statistics.route('/home/statistics')
    @statistics.route('/home/statistics/<any(inventory,usage,waste,savings):tab>')
    @subscription_required
    def page(tab='inventory'):
        try:f=stats.filters(request.args)
        except stats.ReportError as exc:
            f=stats.filters({})
            return failure(tab,f,exc)
        try:
            data=stats.report(session['user_id'],f,tab='use' if tab=='usage' else tab,page=request.args.get('page',1))
            # Resolve only current, visible inventory destinations. Historical/deleted rows remain plain text.
            def destination(row):
                back=url_for('statistics.page',tab=tab,**params(f,page=request.args.get('page',1)),_anchor='statistics-details')
                if row['owner_id']==session['user_id']:return url_for('products.detail',group_id=row['group_id'],return_to=back)
                return url_for('family.inventory',storage_id=row['storage_id'],return_to=back,_anchor='batch-'+str(row['id']))
            for row in data['inventory_rows']+data['risk']:row['url']=destination(row)
            for bucket in data['buckets']:bucket['url']=url_for('statistics.page',tab=tab,**params(f,period='custom',start=bucket['start'].isoformat(),end=bucket['end'].isoformat(),page=1))
            previous_url=url_for('statistics.page',tab=tab,**params(f,period='custom',start=f['previous_start'].isoformat(),end=f['previous_end'].isoformat(),page=1)) if f['previous_start']>=date(2000,1,1) else None
            next_url=url_for('statistics.page',tab=tab,**params(f,period='custom',start=(f['end']+timedelta(days=1)).isoformat(),end=min(stock.today(),f['end']+timedelta(days=f['days'])).isoformat())) if f['end']<stock.today() else None
            table_page=data['inventory_page'] if tab=='inventory' else data['event_page'];pages=data['inventory_pages'] if tab=='inventory' else data['event_pages']
            return render_template('statistics.html',**common(tab,f),data=data,export_url=url_for('statistics.export',tab=tab,**params(f)),
                previous_url=previous_url,next_url=next_url,table_page=table_page,pages=pages,
                table_previous=url_for('statistics.page',tab=tab,**params(f,page=max(1,table_page-1))),table_next=url_for('statistics.page',tab=tab,**params(f,page=table_page+1)),
                first_page=url_for('statistics.page',tab=tab,**params(f,page=1)),
                chart_count=sum(data['current'][key]['count'] for key in data['series']),chart_unknown=sum(data['current'][key]['unknown'] for key in data['series']),
                count_chart_url=url_for('statistics.page',tab=tab,**params(f,chart='count')))
        except stats.ReportError as exc:return failure(tab,f,exc)

    @statistics.route('/statistics/<any(inventory,usage,waste,savings):tab>/export.csv')
    @subscription_required
    def export(tab):
        try:
            f=stats.filters(request.args);data=stats.report(session['user_id'],f,tab='use' if tab=='usage' else tab,export=True)
        except stats.ReportError as exc:abort(exc.status,description=str(exc))
        from services.product_history import safe_cell
        stream=io.StringIO();writer=csv.writer(stream,delimiter=';')
        writer.writerow(['Раздел',TABS[tab]]);writer.writerow(['Область','Общие места семьи' if f['scope']=='family' else 'Мои места'])
        if tab=='inventory':writer.writerow(['Срез запасов',stock.today().strftime('%d.%m.%Y')])
        else:writer.writerow(['Период',f['start'].strftime('%d.%m.%Y'),f['end'].strftime('%d.%m.%Y')])
        if tab=='inventory':
            writer.writerow(['Продукт','Марка','Место','Категория','Количество','Годен до','Известная стоимость остатка, ₽'])
            for row in data['inventory_rows']:writer.writerow([safe_cell(row['name']),safe_cell(row['brand']),safe_cell(row['place']),stock.CATEGORIES[row['category']],amount(row),
                str(row['date'] or ''),format(Decimal(row['cost'])/100,'f') if row['cost'] is not None else ''])
        else:
            writer.writerow(['Дата МСК','Действие','Продукт','Марка','Место','Количество','Известная стоимость, ₽','Причина'])
            for row in data['exports']:writer.writerow([row['stamp'],row['label'],safe_cell(row['name']),safe_cell(row['brand']),safe_cell(row['place']),amount(row),
                format(Decimal(row['cost'])/100,'f') if row['cost'] is not None else '',stats.REASONS.get(row['reason'],'') if row['action']=='waste' else ''])
        return Response('\ufeff'+stream.getvalue(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="statistics-{tab}.csv"','X-Content-Type-Options':'nosniff'})

    @statistics.after_request
    def private(response):response.headers['Cache-Control']='no-store';return response
    app.register_blueprint(statistics)
