from __future__ import annotations

import unittest

from specli.form_family import (
    assemble_fragments,
    canonical_sha256,
    canonical_xml,
    family_xml,
    orphan_xml,
    root_misc_xml,
    split_viewset,
    validate_reference_graph,
)


VIEWSET = """<?xml version="1.0" encoding="UTF-8"?>
<viewset name="Example" custom="kept">
  <!--before views-->
  <views>
    <!--main view-->
    <view name="CollectionObject" class="edu.ku.brc.specify.datamodel.CollectionObject">
      <altviews>
        <altview name="Edit" viewdef="Collection Object" mode="edit"/>
      </altviews>
    </view>
    <view name="CollectionObjectMini" class="edu.ku.brc.specify.datamodel.CollectionObject">
      <altviews>
        <altview name="Edit" viewdef="Collection Object" mode="edit"/>
      </altviews>
    </view>
    <view name="Agent" class="edu.ku.brc.specify.datamodel.Agent">
      <altviews>
        <altview name="Edit" viewdef="Agent" mode="edit"/>
        <altview name="Table" viewdef="Agent Table" mode="view"/>
      </altviews>
    </view>
  </views>
  <!--between-->
  <viewdefs>
    <!--collection form-->
    <viewdef name="Collection Object" type="form">
      <rows><row/></rows>
    </viewdef>
    <viewdef name="Agent" type="form"/>
    <viewdef name="Agent Table" type="formtable">
      <definition>Agent</definition>
    </viewdef>
    <!--legacy orphan-->
    <viewdef name="Unused" type="form"/>
  </viewdefs>
  <!--after defs-->
</viewset>
"""


class FormFamilyTests(unittest.TestCase):
    def test_shared_viewdef_becomes_one_self_contained_family(self) -> None:
        split = split_viewset(VIEWSET, where="fixture")
        self.assertEqual(len(split.families), 2)
        family = split.families[0]
        self.assertEqual(family.table, "collectionobject")
        self.assertEqual(family.name, "collection-object")
        self.assertEqual(
            [item.name for item in family.views],
            ["CollectionObject", "CollectionObjectMini"],
        )
        self.assertEqual(
            [item.name for item in family.viewdefs],
            ["Collection Object"],
        )
        self.assertEqual(
            [item.name for item in split.orphan_viewdefs],
            ["Unused"],
        )

    def test_fragments_roundtrip_canonically(self) -> None:
        split = split_viewset(VIEWSET, where="fixture")
        fragments = [
            (
                f"{family.table}/{family.name}.xml",
                family_xml(family, split.root_attributes),
            )
            for family in split.families
        ]
        assembled = assemble_fragments(
            root_attributes=split.root_attributes,
            family_fragments=fragments,
            view_order=split.view_order,
            viewdef_order=split.viewdef_order,
            orphans=orphan_xml(split.orphan_viewdefs, split.root_attributes),
            root_misc=root_misc_xml(split.root_misc),
        )
        self.assertEqual(canonical_xml(VIEWSET), canonical_xml(assembled))
        self.assertEqual(canonical_sha256(VIEWSET), canonical_sha256(assembled))

    def test_split_is_idempotent(self) -> None:
        first = split_viewset(VIEWSET, where="first")
        fragments = [
            (family.name, family_xml(family, first.root_attributes))
            for family in first.families
        ]
        assembled = assemble_fragments(
            root_attributes=first.root_attributes,
            family_fragments=fragments,
            view_order=first.view_order,
            viewdef_order=first.viewdef_order,
            orphans=orphan_xml(first.orphan_viewdefs, first.root_attributes),
            root_misc=root_misc_xml(first.root_misc),
        )
        second = split_viewset(assembled, where="second")
        self.assertEqual(
            [
                (
                    family.table,
                    family.name,
                    [item.name for item in family.views],
                    [item.name for item in family.viewdefs],
                )
                for family in first.families
            ],
            [
                (
                    family.table,
                    family.name,
                    [item.name for item in family.views],
                    [item.name for item in family.viewdefs],
                )
                for family in second.families
            ],
        )

    def test_dtd_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            split_viewset(
                '<!DOCTYPE x [<!ENTITY y "z">]><viewset name="x"/>',
                where="unsafe",
            )

    def test_cycle_is_rejected(self) -> None:
        xml = """<viewset name="cycle"><views>
          <view name="A" class="x.A"><altviews><altview name="A" viewdef="A"/></altviews></view>
        </views><viewdefs>
          <viewdef name="A"><definition>B</definition></viewdef>
          <viewdef name="B"><definition>A</definition></viewdef>
        </viewdefs></viewset>"""
        with self.assertRaises(SystemExit):
            validate_reference_graph(xml, where="cycle")

    def test_unresolved_reference_is_reported(self) -> None:
        xml = """<viewset name="broken"><views>
          <view name="A" class="x.A"><altviews><altview name="A" viewdef="Missing"/></altviews></view>
        </views><viewdefs/></viewset>"""
        self.assertEqual(
            validate_reference_graph(xml, where="broken"),
            ["Missing"],
        )


if __name__ == "__main__":
    unittest.main()
