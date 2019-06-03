# -*- coding:utf-8 -*-
# ---------------------------------------------------------------------
# 
# ---------------------------------------------------------------------
# Copyright (c) 2017 BDC International Develop Team and Contributors
# All rights reserved.
#
# This is free software; you can redistribute it and/or modify it under
# the terms of the LICENCE attached (see LICENCE file) in the distribution
# package.
#
# Created on 11-04-19

from openerp import models, fields, api
from openerp.tools.translate import _
from odoo import exceptions
from openerp.osv.orm import except_orm
from odoo.exceptions import UserError

class PurchaseOrder(models.Model):
    """
    Purchase Order model customization.
    
    """
    _inherit = 'purchase.order'

    user_department_id = fields.Many2one('hr.department', readonly=True)
    state = fields.Selection([('draft', 'RFQ'),
                              ('sent', 'RFQ Sent'),
                              ('to approve', 'To Approve'),
                              ('purchase', 'Purchase Order'),
                              ('Approved', 'Approved'),
                              ('done', 'Locked'),
                              ('cancel', 'Cancelled')],
                             string='Status', readonly=True,
                             index=True, copy=False,
                             default='draft', track_visibility='onchange')
    @api.model
    def create(self, vals):
        ''' Is necessary redefine for find the department who belong the
        logged user an store in the user_department field.

        '''
        logged_empl = self.env['hr.employee'].search([
            ('user_id', '=', self.env.user.login)])
        if logged_empl.department_id:
            vals['user_department_id'] = logged_empl.department_id.id
        else:
            raise exceptions.ValidationError(
                _('Please select a Department for the logged user.'))
        return super(PurchaseOrder,self).create(vals)

    @api.multi
    def action_view_invoice(self):
        '''Is needed redefine for avoid that the user can create a bill
        without a department manager approved.

        '''
        if self.state == 'Approved':
            return super(PurchaseOrder,self).action_view_invoice()
        else:
            raise exceptions.ValidationError(_('"Error"\
            Please aprove the purchase first..'))

    @api.one
    def aprove_purchase(self):
        ''' Confirm the purchase.
        Also check if the logged user have the needed access for confirm a
        purchase order and raise an exception if not.

        '''
        # groups_name = ['Technical Features']
        groups_name = []
        if self.user_department_id:
            groups_name.append(
                self.user_department_id.name + '_Purchases_Manager')
            groups_name.append(
                self.user_department_id.name + '_Admin_Purchases')
        manager_groups = self.env['res.groups'].search([
            ('name','in', groups_name)
        ])
        flag = False
        for group in manager_groups:
            if self.env.user.id in group.users.ids:
                self.write({'state' : 'Approved'})
                flag = True
        if not flag:
            raise UserError(_('You have no access to confirm a\
                Purchase, please contact with the department manager.'))
        else:
            self.build_invoice()

    def build_invoice(self):
        ''''This method will build an invoice using the purchase fields and
        related with the PO.

        '''
        AccountInvoice = self.env['account.invoice']
        AccountJournal = self.env['account.journal']
        purchaseJournal = AccountJournal.search([('type','=','purchase')])
        if not purchaseJournal:
            raise UserError(_('Please create a purchase type journal.'))
        p_expense_acc = self.order_line.product_id.property_account_expense_id
        invoice = AccountInvoice.create({
            'partner_id' : self.partner_id.id,
            'currency_id' : self.company_id.currency_id.id,
            'journal_id' : purchaseJournal.id,
            'company_id' : self.company_id.id,
            'purchase_id' : self.id,
            'origin' : self.name,
            'state' : 'draft',
            'type' : 'in_invoice',
            'user_id' : self.user_id.id,
            'create_date' : self.create_date,
            'write_uid' : self.write_uid.id,
            'write_date' : self.write_date,
            'invoice_line_ids' :
            [
            (0, 0, {
                'name': self.name + ':' +self.product_id.name,
                'origin': self.name,
                'uom_id': self.order_line.product_uom.id,
                'product_id': self.order_line.product_id.id,
                'account_id': p_expense_acc.id,
                'price_unit': self.order_line.price_unit,
                'price_subtotal': self.order_line.price_subtotal,
                'price_total': self.order_line.price_total,
                'quantity': self.order_line.product_qty,
                'company_id': self.company_id.id,
                'partner_id': self.partner_id.id,
                'currency_id': self.company_id.currency_id.id,
                'create_uid': self.create_uid.id,
                'create_date': self.create_date,
                'write_uid': self.write_uid.id,
                'write_date': self.write_date,
                'purchase_line_id': self.order_line.id})
            ]
        })
        invoice.action_invoice_open()


class HrDepartment(models.Model):
    """
    Hr Department Model customization.
    
    """
    _inherit = 'hr.department'

    @api.multi
    def write(self, vals):
        ''' Is necessary redefine because when the user change the name of
        a department, the created groups and its rules must be updated with
        the new name.

        '''
        IrModuleCat = self.env['ir.module.category']
        ResGroups = self.env['res.groups']
        IrRule = self.env['ir.rule']
        # updating the category.
        category = IrModuleCat.sudo().search([
            ('name','=',self.name + " Deparment")])
        category.name = vals.get('name') + " Deparment"
        # updating the user group and rule
        user_g = ResGroups.search([
            ('name','=','{dpto}_Purchases_User'.format(dpto = self.name))])
        user_g.name = '{dpto}_Purchases_User'.format(dpto=vals.get('name'))
        user_rule = IrRule.search([
            ('name','=','Custom_Purchase_User_Rule_{dpto}'.format(
                dpto=self.name))])
        user_rule.name = 'Custom_Purchase_User_Rule_{dpto}'.format(
            dpto=vals.get('name'))
        # updating the manager group and rule
        manag_g = ResGroups.search([
            ('name','=','{dpto}_Purchases_Manager'.format(dpto=self.name))])
        manag_g.name = '{dpto}_Purchases_Manager'.format(dpto=vals.get('name'))
        manag_rule = IrRule.search([
            ('name','=', 'Custom_Purchase_Manager_Rule_{dpto}'.format(
                dpto=self.name))])
        manag_rule.name = 'Custom_Purchase_Manager_Rule_{dpto}'.format(
                dpto=vals.get('name'))
        # updating the admin group and rule
        admin_group = ResGroups.search([
            ('name','=', '{dpto}_Admin_Purchases'.format(dpto=self.name))])
        admin_group.name = '{dpto}_Admin_Purchases'.format(dpto=vals.get('name'))
        admin_rule = IrRule.search([
            ('name', '=', 'Custom_Purchases_Admin_Rule_{dpto}'.format(
                dpto=self.name)
            )])
        admin_rule.name = 'Custom_Purchases_Admin_Rule_{dpto}'.format(
                dpto=vals.get('name'))
        return super(HrDepartment, self).write(vals)

    @api.model
    def create(self, vals):
        ''' Create the category, groups related with this new 
        department so the users could have two roles; user or manager
        and each one of this have diferents access level.

        '''
        IrModuleCat = self.env['ir.module.category']
        ResGroups = self.env['res.groups']
        IrRule = self.env['ir.rule']
        dptoCateg = IrModuleCat.sudo().create({
            'name' : vals.get('name') + " Deparment",
            'description' : 'Custom Purchase {dptoName}'.format(
                dptoName=vals.get('name', '')),
            'sequence' : 1
        })
        purchase_model_id = self.env['ir.model'].search(
            [('name', '=', 'Purchase Order')])        
        group_user = ResGroups.create({
            'name': '{dptoName}_Purchases_User'.format(
                dptoName=vals.get('name')),
            'category_id' : dptoCateg.id,
        })
        user_domain = "[('create_uid','=',user.id)]"
        userRule = IrRule.create({
            'name': 'Custom_Purchase_User_Rule_{dptoName}'.format(
                dptoName=vals.get('name')),
            'model_id': purchase_model_id.id,
            'groups': group_user,
            'domain_force': user_domain
        })
        userRule.groups = group_user        
        group_manager = ResGroups.create({
            'name': '{dptoName}_Purchases_Manager'.format(
                dptoName=vals.get('name')),
            'category_id' : dptoCateg.id,
        })
        manager_domain = "['|', ('create_uid', '=', user.id),\
        ('user_department_id.member_ids.user_id', 'in', [user.id])]"
        managerRule = IrRule.create({
            'name': 'Custom_Purchase_Manager_Rule_{dptoName}'.format(
                dptoName=vals.get('name')),
            'model_id': purchase_model_id.id,
            'groups': group_manager,
            'domain_force': manager_domain
        })
        managerRule.groups = group_manager
        # Creating the admin group and rule.
        group_admin = ResGroups.create({
            'name': '{dptoName}_Admin_Purchases'.format(
                dptoName=vals.get('name')),
            'category_id' : dptoCateg.id,
        })
        managerRule = IrRule.create({
            'name': 'Custom_Purchases_Admin_Rule_{dptoName}'.format(
                dptoName=vals.get('name')),
            'model_id': purchase_model_id.id,
            'groups': group_admin,
            'domain_force': "[(1,'=',1)]"
        })
        managerRule.groups = group_admin
        return super(HrDepartment,self).create(vals)
