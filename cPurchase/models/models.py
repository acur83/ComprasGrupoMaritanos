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
from odoo.addons import decimal_precision as dp

class HrEmployee(models.Model):
    """
    Redefined for update department field.
    """
    _inherit = 'hr.employee'

    department_id = fields.Many2one('hr.department', required=True)

class AccountInvoiceLine(models.Model):
    """
    Account Invoice Line model customization.

    """
    _inherit = 'account.invoice.line'

    computed_quantity = fields.Float(compute='_get_quantity',
                                     store=True,
                                     string='Quantity')

    @api.model
    def create(self, vals):
        '''Set the quantity value.
        Also create the relationship between the invoice and the
        account.invoice.tax using the invoice line fields.

        '''
        invoice_line = super(AccountInvoiceLine,self).create(vals)
        invoice_line.write({'quantity': invoice_line.computed_quantity})
        tax_amount = (
            invoice_line.price_subtotal * invoice_line.invoice_line_tax_ids.amount)/100
        AccountInvoiceTax = self.env['account.invoice.tax']
        line_tax = AccountInvoiceTax.create(
            dict(name=invoice_line.invoice_line_tax_ids.name,
                 account_id=invoice_line.account_id.id,
                 amount= tax_amount,
                 invoice_id=invoice_line.invoice_id.id))
        return invoice_line

    @api.depends('product_id')
    def _get_quantity(self):
        '''Calculate the product quantity using the purchase line object. '''
        for record in self:
            record.computed_quantity = record.purchase_line_id.product_qty


class AccountInvoice(models.Model):
    """
    Account Invoice model customization.
    """
    _inherit = 'account.invoice'

    @api.multi
    def _get_purchase_origin(self, purchase_name):
        '''Find and return the origin in the case of Purchase Order.
        '''
        PurchaseOrder = self.env['purchase.order']
        return PurchaseOrder.search([('name', '=', purchase_name)])

    @api.model
    def create(self, vals):
        '''Find the related Purchase Order and set the invoiced state.
        Also update in the new invoice the field date_invoice obtained from
        the Purchase Order.
        '''
        invoice = super(AccountInvoice,self).create(vals)
        if (self.env.context.get('active_model', False) == 'purchase.order'
            and invoice.origin):
            purchase = self._get_purchase_origin(invoice.origin)
            if any(purchase):
                invoice.date_invoice = str(purchase.date_order.date())
                purchase.write({'state' : 'invoiced'})
        invoice.action_invoice_open()
        invoice._compute_amount()
        # remove the account.invoice.tax with amount equal 0
        [l.unlink() for l in invoice.tax_line_ids if l.amount==0]
        return invoice

    @api.multi
    def write(self, vals):
        '''Change the Purchase Order state to done when the user paid the
        invoice.

        '''
        if vals.get('state', False):
            if vals['state'] == 'paid':
                purchase_origin = self._get_purchase_origin(self.origin)
                if any(purchase_origin):
                    purchase_origin.write({'state' : 'purchase_done'})
        return super(AccountInvoice, self).write(vals)


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
                              ('purchase_done', 'Done'),
                              ('invoiced', 'Invoiced'),
                              ('done', 'Locked'),
                              ('cancel', 'Cancelled')],
                             string='Status', readonly=True,
                             index=True, copy=False,
                             default='draft', track_visibility='onchange')
    @api.multi
    def button_confirm(self):
        ''' Set the needed state purchase.
        The odoo default behavior set the state 'to approve'.
        '''
        for order in self:
            if order.state not in ['draft', 'sent']:
                continue
            order._add_supplier_to_product()
            # Deal with double validation process
            if order.company_id.po_double_validation == 'one_step'\
                    or (order.company_id.po_double_validation == 'two_step'\
                        and order.amount_total < self.env.user.company_id.currency_id._convert(
                            order.company_id.po_double_validation_amount,
                            order.currency_id, order.company_id,
                            order.date_order or fields.Date.today()))\
                    or order.user_has_groups('purchase.group_purchase_manager'):
                order.button_approve()
            else:
                order.write({'state': 'purchase'})
        return True

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
    def button_approve(self, force=False):
        ''' Redefined for avoid the odoo default behavior who write the
        state 'done'('block').
        '''
        self.write({'state': 'purchase',
                    'date_approve': fields.Date.context_today(self)})
        return {}

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
                self.user_department_id.name + '_Purchases_Admin')
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
        AccountInvoiceLine = self.env['account.invoice.line']
        AccountJournal = self.env['account.journal']
        AccountInvoiceTax = self.env['account.invoice.tax']
        purchaseJournal = AccountJournal.search([('type','=','purchase')])
        if not purchaseJournal:
            raise UserError(_('Please create a purchase type journal.'))
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
            'write_date' : self.write_date})
        for line in self.order_line:
            expense_acc = line.product_id.property_account_expense_id
            prod_categ = line.product_id.categ_id
            while not expense_acc and prod_categ:
                if prod_categ.property_account_expense_categ_id:
                    expense_acc = prod_categ.property_account_expense_categ_id
                if prod_categ.parent_id:
                    prod_categ = prod_categ.parent_id
                else:
                    prod_categ = False
            inv_line = AccountInvoiceLine.create({
                'name': line.name + ':' + line.product_id.name,
                'origin': line.name,
                'uom_id': line.product_uom.id,
                'product_id': line.product_id.id,
                'account_id': expense_acc.id,
                'price_unit': line.price_unit,
                'price_subtotal': line.price_subtotal,
                'price_total': line.price_total,
                'quantity': line.product_qty,
                'company_id': line.company_id.id,
                'partner_id': line.partner_id.id,
                'currency_id': line.company_id.currency_id.id,
                'create_uid': line.create_uid.id,
                'create_date': line.create_date,
                'write_uid': line.write_uid.id,
                'write_date': line.write_date,
                'invoice_line_tax_ids': [(6, 0, [t.id for t in line.taxes_id])],
                'purchase_line_id': line.id})
            inv_line.invoice_id = invoice.id
            if line.taxes_id:
                tax_amount = (line.price_subtotal * line.taxes_id.amount)/100
                line_tax = AccountInvoiceTax.create(
                    dict(name=line.taxes_id.name,
                         account_id=expense_acc.id,
                         amount= tax_amount,
                         invoice_id=invoice.id))
            invoice.write({'date_invoice' : str(self.date_order.date())})
            inv_residual = sum([l.price_total for l in self.order_line])
            # It is amazing if put the result needed like residual in the same
            # write method call does not work ...
            invoice.write({'residual' : inv_residual ,
                           'residual_signed' : inv_residual,
                           'residual_company_signed' : inv_residual
            })
            # invoice._compute_amount()


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
        if vals.get('name', False):
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
                ('name','=', '{dpto}_Purchases_Admin'.format(dpto=self.name))])
            admin_group.name = '{dpto}_Purchases_Admin'.format(dpto=vals.get('name'))
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
        ModelAccess = self.env['ir.model.access']
        dptoCateg = IrModuleCat.sudo().create({
            'name' : vals.get('name') + " Deparment",
            'description' : 'Custom Purchase {dptoName}'.format(
                dptoName=vals.get('name', '')),
            'sequence' : 1
        })
        purchase_model_id = self.env['ir.model'].search(
            [('model', '=', 'purchase.order')])

        po_user_group = self.env.ref('purchase.group_purchase_user')
        group_user = ResGroups.create({
            'name': '{dptoName}_Purchases_User'.format(
                dptoName=vals.get('name')),
            'category_id' : dptoCateg.id,
            'implied_ids' : [(4, po_user_group.id)]
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
        po_manager_group = self.env.ref('purchase.group_purchase_manager')
        group_manager = ResGroups.create({
            'name': '{dptoName}_Purchases_Manager'.format(
                dptoName=vals.get('name')),
            'category_id' : dptoCateg.id,
            'implied_ids' : [(4, po_manager_group.id)]
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
        model_ids = [m.id for m in self.env['ir.model'].search(
            ['|','|','|',
             ('model', '=', 'account.partial.reconcile'),
             ('model', '=', 'account.move'),
             ('model', '=', 'account.account.type'),
             ('model', '=', 'account.move.line')])]
        for model in model_ids:
            ModelAccess.create(
                dict(name='po_manager_access_move',
                     model_id=model,
                     group_id=group_manager.id,
                     perm_read=True,
                     perm_write=True,
                     perm_create=True,
                     perm_unlink=True))

        # Creating the admin group and rule.
        po_admin_group = self.env.ref('base.group_system')
        group_admin = ResGroups.create({
            'name': '{dptoName}_Purchases_Admin'.format(
                dptoName=vals.get('name')),
            'category_id' : dptoCateg.id,
            'implied_ids' : [(4, po_admin_group.id),
                             (4, po_manager_group.id)]
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

    @api.multi
    def unlink(self):
        '''Is necessary remove the related object created.
        Remove the department category, groups and rules defined in the
        department creation.

        '''
        IrModuleCat = self.env['ir.module.category']
        ResGroups = self.env['res.groups']
        IrRule = self.env['ir.rule']
        user_g = ResGroups.search([
            ('name','=','{dpto}_Purchases_User'.format(dpto = self.name))])
        user_rule = IrRule.search([
            ('name','=','Custom_Purchase_User_Rule_{dpto}'.format(
                dpto=self.name))])
        user_rule.unlink()
        user_g.unlink()
        manag_g = ResGroups.search([
            ('name','=','{dpto}_Purchases_Manager'.format(dpto=self.name))])
        manag_rule = IrRule.search([
            ('name','=', 'Custom_Purchase_Manager_Rule_{dpto}'.format(
                dpto=self.name))])
        manag_g.unlink()
        manag_rule.unlink()
        admin_group = ResGroups.search([
                ('name','=', '{dpto}_Purchases_Admin'.format(dpto=self.name))])
        admin_rule = IrRule.search([
                ('name', '=', 'Custom_Purchases_Admin_Rule_{dpto}'.format(
                    dpto=self.name)
                )])
        admin_group.unlink()
        admin_rule.unlink()
        category = IrModuleCat.sudo().search([
            ('name', '=', self.name + " Deparment")])
        category.unlink()
        return super(HrDepartment, self).unlink()
