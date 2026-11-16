"""Run: python -m unittest discover -s tests -p test_dietary.py"""
import unittest
from services import dietary

class DietaryTests(unittest.TestCase):
    def recipe(self,*names):
        return {'ingredients':[{'name':n} for n in names]}
    def profile(self,**values):
        return {'dietary':dietary.DEFAULTS|values}
    def test_milk_derivatives_and_lactose_free(self):
        for name in ['Сыр','Творог','Молоко без лактозы','Сыворотка']:
            with self.subTest(name=name):
                self.assertFalse(dietary.assess(self.recipe(name),self.profile(allergens=['milk']))['eligible'])
    def test_egg_and_nuts(self):
        for allergen,name in [('egg','Желтки'),('nuts','Миндальная мука'),('peanut','Арахисовая паста')]:
            self.assertFalse(dietary.assess(self.recipe(name),self.profile(allergens=[allergen]))['eligible'])
    def test_unknown_composite_and_empty_recipe(self):
        self.assertFalse(dietary.assess(self.recipe('Соус'),self.profile(allergens=['milk']))['eligible'])
        self.assertFalse(dietary.assess(self.recipe(),self.profile())['eligible'])
    def test_no_match_still_requires_label_review(self):
        result=dietary.assess(self.recipe('Картофель'),self.profile(allergens=['milk']))
        self.assertTrue(result['eligible'])
        self.assertTrue(result['review'])
    def test_celiac_and_child(self):
        self.assertFalse(dietary.assess(self.recipe('Перловка'),self.profile(condition='celiac'))['eligible'])
        self.assertFalse(dietary.assess(self.recipe('Вино'),self.profile(audience='child'))['eligible'])
        self.assertFalse(dietary.assess(self.recipe('Картофель'),self.profile(audience='infant'))['eligible'])
    def test_portions_and_unknown_nutrients(self):
        p=self.profile(carb_limit='30')
        self.assertTrue(dietary.assess(self.recipe('Рис'),p,{'carbs':'20'},1)['eligible'])
        self.assertFalse(dietary.assess(self.recipe('Рис'),p,{'carbs':'20'},2)['eligible'])
        self.assertFalse(dietary.assess(self.recipe('Рис'),p,None)['eligible'])
    def test_invalid_settings(self):
        for data in [{'allergens':['unknown']},{'audience':'unknown'},{'carb_limit':'NaN'},{'carb_limit':'-1'}]:
            with self.subTest(data=data),self.assertRaises(ValueError):dietary.validate(data)
    def test_medical_exclusion(self):
        self.assertFalse(dietary.assess(self.recipe('Креветки'),self.profile(medical_excluded='креветки'))['eligible'])

if __name__=='__main__':unittest.main()
