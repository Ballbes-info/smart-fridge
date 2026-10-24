"""Recalculate recipe categories from stored tags. Run: python -m services.recipe_categories"""
import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from services import recipe_catalog as catalog


def fix_categories(db_path, *, dry_run=False):
    con=sqlite3.connect(str(db_path))
    con.row_factory=sqlite3.Row
    try:
        fts=con.execute("SELECT value FROM metadata WHERE key='fts'").fetchone()
        has_fts=fts is not None and fts[0]=='1'

        rows=con.execute('SELECT id,category,data FROM recipes').fetchall()
        new_dist=Counter()
        changed=0
        start=time.monotonic()

        for row in rows:
            recipe=json.loads(row['data'])
            new_category=catalog.pick_category(row['category'],*recipe.get('tags',[]))
            new_dist[new_category or '(пусто)']+=1
            if new_category==row['category']:
                continue
            changed+=1
            if dry_run:continue
            recipe['category']=new_category
            search_text=' '.join([recipe['title'],new_category,recipe.get('cuisine',''),
                *recipe.get('tags',[]),*[i['name'] for i in recipe.get('ingredients',[])]]).casefold().replace('ё','е')
            con.execute('UPDATE recipes SET category=?,search_text=?,data=? WHERE id=?',
                (new_category,search_text,json.dumps(recipe,ensure_ascii=False),row['id']))
            if has_fts:
                con.execute('DELETE FROM recipes_fts WHERE rowid=?',(row['id'],))
                con.execute('INSERT INTO recipes_fts(rowid,search_text) VALUES(?,?)',(row['id'],search_text))
            if changed%5000==0:
                con.commit()
                print(f'Обновлено {changed:,} записей…',flush=True)

        con.commit()
        elapsed=time.monotonic()-start
        print(f'Готово за {elapsed:.1f} с. Проверено {len(rows):,}, изменено {changed:,}, без изменений {len(rows)-changed:,}.',flush=True)
        print('\nРаспределение категорий после исправления:')
        for name,count in new_dist.most_common():
            print(f'  {count:7,}  {name}')
        return new_dist
    finally:
        con.close()


def main():
    parser=argparse.ArgumentParser(description='Пересчёт категорий рецептов по справочнику из recipe_catalog.py.')
    parser.add_argument('--db',help='Файл каталога (по умолчанию db/recipes.db)')
    parser.add_argument('--dry-run',action='store_true',help='Показать что изменится, не записывая в базу')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(catalog.ROOT/'.env')
    path=args.db or catalog.catalog_path()
    if not path.is_file():
        print(f'Файл не найден: {path}',file=sys.stderr);return 1
    try:
        fix_categories(path,dry_run=args.dry_run)
    except (sqlite3.Error,ValueError,OSError) as exc:
        print('Ошибка: '+str(exc),file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
